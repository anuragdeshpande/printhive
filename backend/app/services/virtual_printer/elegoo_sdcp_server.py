import asyncio
import json
import logging
import re
import time
import websockets

logger = logging.getLogger(__name__)


class ElegooSDCPServer:
    """Async WebSocket server operating on port 3030 for OrcaSlicer Elegoo Link (SDCP) protocol.

    OrcaSlicer's Elegoo Link client connects to ws://<host>:3030/websocket after uploading
    a print file to issue the start-print command and check SDCP printer status.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 3030):
        self.host = host
        self.port = port
        self._server = None
        self._print_options: dict[str, dict] = {}
        self.ready = asyncio.Event()

    def get_print_options(self, filename: str) -> dict | None:
        """Get cached SDCP print options for a filename."""
        if not filename:
            return None
        clean_key = filename.replace("\\", "/").split("/")[-1]
        if clean_key in self._print_options:
            return self._print_options[clean_key]

        # Normalized matching (ignoring spaces, underscores, plus signs, case)
        norm_key = re.sub(r"[\s_+.-]+", "", clean_key.lower())
        for k, v in self._print_options.items():
            norm_k = re.sub(r"[\s_+.-]+", "", k.lower())
            if norm_k == norm_key or (len(norm_k) > 5 and norm_k in norm_key) or (len(norm_key) > 5 and norm_key in norm_k):
                return v

        stem = clean_key.split(".")[0]
        for k, v in self._print_options.items():
            if k.startswith(stem) or stem.startswith(k.split(".")[0]):
                return v
        return None

    async def _update_pending_queue_item(self, filename: str, cali_switch: int | None, tlp_switch: int | None):
        """Update bed_levelling and timelapse on pending queue item if it matches the filename."""
        try:
            from backend.app.core.database import async_session
            from backend.app.models.print_queue import PrintQueueItem
            from backend.app.models.archive import PrintArchive
            from sqlalchemy import select

            async with async_session() as db:
                stmt = (
                    select(PrintQueueItem, PrintArchive)
                    .join(PrintArchive, PrintQueueItem.archive_id == PrintArchive.id)
                    .where(PrintQueueItem.status == "pending")
                )
                result = await db.execute(stmt)
                target_filename = filename.replace("\\", "/").split("/")[-1]
                rows = result.all()
                matching_items = [
                    item
                    for item, archive in rows
                    if archive.filename.replace("\\", "/").split("/")[-1] == target_filename
                ]
                for item in matching_items:
                    if cali_switch is not None:
                        item.bed_levelling = "on" if cali_switch == 1 else "off"
                    if tlp_switch is not None:
                        item.timelapse = bool(tlp_switch)
                if matching_items:
                    await db.commit()
                    logger.info(
                        "Updated %d pending queue item(s) with SDCP options (file=%s, cali=%s, tlp=%s)",
                        len(matching_items),
                        target_filename,
                        cali_switch,
                        tlp_switch,
                    )
        except Exception as e:
            logger.debug("Could not update pending queue item from SDCP options: %s", e)

    async def _handle_connection(self, websocket, path: str = ""):
        remote = getattr(websocket, "remote_address", "unknown")
        logger.info("Elegoo SDCP WebSocket client connected from %s (path=%s)", remote, path)
        try:
            async for message in websocket:
                logger.info("Elegoo SDCP WS received: %s", message)
                try:
                    data = json.loads(message)
                    req_data = data.get("Data", {})
                    cmd = req_data.get("Cmd")
                    req_id = req_data.get("RequestID", "")
                    mainboard_id = req_data.get("MainboardID") or "20P90A391800002"

                    if cmd == 0:  # ELEGOO_GET_STATUS
                        resp = {
                            "Id": req_id,
                            "Status": {
                                "CurrentStatus": [0],
                                "PrintStatus": 0,
                            },
                            "Data": {
                                "Cmd": 0,
                                "Data": {"Ack": 0},
                                "RequestID": req_id,
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time() * 1000),
                            },
                            "Topic": f"sdcp/response/{mainboard_id}",
                        }
                        await websocket.send(json.dumps(resp))

                    elif cmd == 1:  # ELEGOO_GET_PROPERTIES
                        resp = {
                            "Id": req_id,
                            "Attributes": {
                                "Name": "Virtual Centauri Carbon",
                                "MachineName": "Centauri Carbon",
                                "BrandName": "ELEGOO",
                                "ProtocolVersion": "V1.0.0",
                                "FirmwareVersion": "V1.0.0",
                                "MainboardID": mainboard_id,
                                "Resolution": "256x256x256",
                            },
                            "Data": {
                                "Cmd": 1,
                                "Data": {"Ack": 0},
                                "RequestID": req_id,
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time() * 1000),
                            },
                            "Topic": f"sdcp/response/{mainboard_id}",
                        }
                        await websocket.send(json.dumps(resp))

                    elif cmd == 128:  # ELEGOO_START_PRINT
                        file_info = req_data.get("Data", {})
                        filename = file_info.get("Filename", "")
                        platform_type = file_info.get("PrintPlatformType")
                        cali_switch = file_info.get("Calibration_switch")
                        tlp_switch = file_info.get("Tlp_Switch")
                        logger.info(
                            "Elegoo SDCP Start Print command for file %s (platform_type=%s, cali=%s, tlp=%s)",
                            filename,
                            platform_type,
                            cali_switch,
                            tlp_switch,
                        )
                        ack = 0
                        if not filename:
                            ack = 2  # SDCP_PRINT_CTRL_ACK_NOT_FOUND

                        if filename:
                            clean_key = filename.replace("\\", "/").split("/")[-1]
                            self._print_options[clean_key] = {
                                "platform_type": platform_type,
                                "calibration_switch": cali_switch,
                                "tlp_switch": tlp_switch,
                                "raw_data": file_info,
                                "timestamp": time.time(),
                            }
                            # Asynchronously update any pending queue items with these options
                            asyncio.create_task(self._update_pending_queue_item(clean_key, cali_switch, tlp_switch))

                        resp = {
                            "Id": req_id,
                            "Data": {
                                "Cmd": 128,
                                "Data": {
                                    "Ack": ack
                                },
                                "RequestID": req_id,
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time() * 1000),
                            },
                            "Topic": f"sdcp/response/{mainboard_id}",
                        }
                        await websocket.send(json.dumps(resp))

                    elif cmd == 129:  # ELEGOO_PAUSE_PRINT
                        try:
                            from backend.app.services.printer_manager import printer_manager
                            from backend.app.services.elegoo_client import is_elegoo_model
                            for pid, p in printer_manager._printers.items():
                                if is_elegoo_model(getattr(p, "model", None)):
                                    printer_manager.pause_print(pid)
                        except Exception as err:
                            logger.debug("Failed to delegate pause command: %s", err)
                        resp = {
                            "Id": req_id,
                            "Data": {
                                "Cmd": 129,
                                "Data": {"Ack": 0},
                                "RequestID": req_id,
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time() * 1000),
                            },
                            "Topic": f"sdcp/response/{mainboard_id}",
                        }
                        await websocket.send(json.dumps(resp))

                    elif cmd == 130:  # ELEGOO_STOP_PRINT
                        try:
                            from backend.app.services.printer_manager import printer_manager
                            from backend.app.services.elegoo_client import is_elegoo_model
                            for pid, p in printer_manager._printers.items():
                                if is_elegoo_model(getattr(p, "model", None)):
                                    printer_manager.stop_print(pid)
                        except Exception as err:
                            logger.debug("Failed to delegate stop command: %s", err)
                        resp = {
                            "Id": req_id,
                            "Data": {
                                "Cmd": 130,
                                "Data": {"Ack": 0},
                                "RequestID": req_id,
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time() * 1000),
                            },
                            "Topic": f"sdcp/response/{mainboard_id}",
                        }
                        await websocket.send(json.dumps(resp))

                    elif cmd == 131:  # ELEGOO_RESUME_PRINT
                        try:
                            from backend.app.services.printer_manager import printer_manager
                            from backend.app.services.elegoo_client import is_elegoo_model
                            for pid, p in printer_manager._printers.items():
                                if is_elegoo_model(getattr(p, "model", None)):
                                    printer_manager.resume_print(pid)
                        except Exception as err:
                            logger.debug("Failed to delegate resume command: %s", err)
                        resp = {
                            "Id": req_id,
                            "Data": {
                                "Cmd": 131,
                                "Data": {"Ack": 0},
                                "RequestID": req_id,
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time() * 1000),
                            },
                            "Topic": f"sdcp/response/{mainboard_id}",
                        }
                        await websocket.send(json.dumps(resp))
                    else:
                        resp = {
                            "Id": req_id,
                            "Status": {"CurrentStatus": [0]},
                            "Data": {
                                "Cmd": cmd or 0,
                                "Data": {"Ack": 0},
                                "RequestID": req_id,
                                "MainboardID": mainboard_id,
                                "TimeStamp": int(time.time() * 1000),
                            },
                            "Topic": f"sdcp/response/{mainboard_id}",
                        }
                        await websocket.send(json.dumps(resp))
                except Exception as e:
                    logger.error("Error processing Elegoo SDCP WS message: %s", e)
        except Exception as e:
            logger.info("Elegoo SDCP WS client disconnected: %s", e)

    async def start(self):
        try:
            self._server = await websockets.serve(self._handle_connection, self.host, self.port)
            self.ready.set()
            logger.info("Elegoo SDCP WebSocket server listening on %s:%d", self.host, self.port)
        except Exception as e:
            logger.error("Failed to start Elegoo SDCP WebSocket server on port %d: %s", self.port, e)

    async def stop(self):
        self.ready.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Elegoo SDCP WebSocket server stopped")


elegoo_sdcp_server = ElegooSDCPServer()
