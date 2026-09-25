# AGENTS.md

Instructions, architecture rules, and upstream merge protocols for AI coding assistants working in the PrintHive codebase.

---

## 1. Project Overview & Mission

**PrintHive** is an open-source, multi-vendor 3D print farm management platform and print orchestrator forked from Bambuddy (`maziggy/bambuddy`).

### Core Purpose of This Fork
While upstream Bambuddy focuses primarily on Bambu Lab printers, the primary reason for this fork is **first-class, native multi-vendor printer integration**:
- **Elegoo Centauri Series (CC1, CC2)** via SDCP (WebSocket port 3030 and HTTP chunked uploads).
- **Flashforge Series (Creator 5)** multi-toolhead printers.
- **Embedded PyCentauri library** (`pycentauri/`) providing protocol connectivity and reverse-engineered SDCP APIs.
- **Virtual Printer Multi-Brand Emulation**: Providing local virtual SDCP endpoints (port 3030) so OrcaSlicer and other slicers can treat PrintHive as an Elegoo or Bambu printer.
- **Mobile-First UX & Android Integration**: Dedicated Android live activities, mobile modal bottom sheet navigation, touch drag queue reordering, and sliding window token auth.

---

## 2. Critical Constraints & Operational Rules

1. **Local Docker Builds Only**:
   - **NEVER** run Docker builds or compile frontend bundles on remote production machines (Proxmox host or LXC containers).
   - **ALWAYS** build the Docker image locally using OrbStack / Docker (`docker build -t printhive:hardened .`) on the developer machine, then stream the image to Proxmox and load it via `docker load`.
2. **Proxmox LXC Lifecycle**:
   - Production runs on LXC 106 (`192.168.1.241`).
   - LXC 105 is the legacy container and must **NEVER** be started or run concurrently with LXC 106.
   - Clean up any temporary image files (`/var/lib/vz/dump/printhive.tar`, `/tmp/printhive.tar`) on host and container after loading.
3. **Printer IPs & Network**:
   - BambuLab X2D: `192.168.1.113` (MQTT port 8883, FTPS port 990, RTSP camera).
   - Elegoo Centauri Carbon (CC1): `192.168.1.235` (SDCP WebSocket port 3030, HTTP upload port 80/chunked).

---

## 3. Upstream Merge Protocol (MUST FOLLOW ON EVERY MERGE)

When merging upstream updates (e.g., from `https://github.com/maziggy/bambuddy.git` `main`), upstream code will often overwrite or omit fork-specific customizations during 3-way merge resolution. 

### Mandatory Pre-Merge & Post-Merge Reconnaissance Check
Before merging, record the pre-merge commit hash (e.g. `PRE_MERGE_HASH`). After resolving conflicts, execute an automated check to verify that custom multi-vendor references have **not** been reduced:

```bash
python3 -c '
import subprocess
def run(cmd): return subprocess.check_output(cmd, shell=True, text=True)
files = [f.split(":", 1)[1] for f in run("git grep -i -l -E \"elegoo|centauri|flashforge|pycentauri|sdcp\" PRE_MERGE_HASH").strip().splitlines()]
for f in files:
    c_old = run(f"git grep -i -c -E \"elegoo|centauri|flashforge|pycentauri|sdcp\" PRE_MERGE_HASH -- \"{f}\" || true").strip().split(":")[-1] or "0"
    c_new = run(f"git grep -i -c -E \"elegoo|centauri|flashforge|pycentauri|sdcp\" HEAD -- \"{f}\" || true").strip().split(":")[-1] or "0"
    if int(c_new) < int(c_old):
        print(f"ALERT: Reference count dropped in {f}: {c_old} -> {c_new}")
'
```

### Critical Files That Must Never Lose Multi-Vendor Code

#### A. Backend Core & Services
1. **`backend/app/main.py`**:
   - Connection Watchdog: Must remain vendor-aware. Never probe port `8883` on an Elegoo printer (`is_elegoo_model()`) or call Bambu MQTT disconnect methods on SDCP clients. Probe port `3030` for Elegoo.
   - Guard `_last_message_time` checks using `getattr(client, "_last_message_time", None)`.
   - Preserve `PREPARE` state handling for cover URLs and active print status restoration on restart.
2. **`backend/app/services/elegoo_client.py` & `pycentauri/`**:
   - Must remain fully intact with `_last_message_time`, `last_connect_error`, and `force_reconnect_stale_session`.
   - PyCentauri package directory must be preserved and copied into Docker image (`COPY pycentauri/ /app/pycentauri/`).
3. **`backend/app/services/flashforge_client.py`**:
   - Must remain intact for Creator 5 multi-toolhead printer support.
4. **`backend/app/services/virtual_printer/elegoo_sdcp_server.py`**:
   - Port 3030 WebSocket server must remain initialized and listening for OrcaSlicer virtual printer communication.
5. **`backend/app/services/print_scheduler.py`**:
   - Must preserve `upload_elegoo_file_async` (1MB chunked multipart upload).
   - Must preserve `derive_elegoo_remote_filename` (`.gcode` extension for Elegoo CC1 vs `.3mf` for Bambu).
   - Must preserve Elegoo SDCP bed type resolution.
6. **`backend/app/schemas/printer.py`**:
   - `access_code` on `PrinterCreate` must remain optional for Elegoo models (`model_validator`).
   - `plate_detection_enabled` must be included on `PrinterBase`.
7. **`backend/app/utils/printer_models.py`**:
   - Must include `CC1`, `CC2`, and `Creator 5` mappings in `PRINTER_MODEL_MAP` and `PRINTER_MODEL_ID_MAP`.

#### B. Frontend Pages & Modals
1. **`frontend/src/pages/PrintersPage.tsx`**:
   - **`AddPrinterModal` & `EditPrinterModal`**:
     - Both modals MUST contain model options for:
       - `Elegoo — Centauri Series`: `Centauri Carbon (CC1)`, `Centauri Carbon 2 (CC2)`
       - `Flashforge Series`: `Creator 5`
     - `access_code` must be optional for Elegoo models.
     - Connection preflight diagnostics must skip Bambu MQTT/FTPS checks when saving Elegoo models.
     - `EditPrinterModal` MUST contain:
       - **Camera Settings**: External camera toggle, protocol select (`mjpeg`, `rtsp`, `snapshot`, `usb`), stream URL, snapshot URL, camera rotation.
       - **Plate Detection / Clear Plate Verification**: Checkbox to gate print queue dispatch.
2. **`frontend/src/components/Layout.tsx`**:
   - Mobile navigation must use the modal bottom sheet (`isSidebarCompact && mobileDrawerOpen`) with swipe-down-to-dismiss gestures.
3. **`frontend/src/pages/QueuePage.tsx`**:
   - Mobile touch drag-and-drop handles for reordering print queue items.
4. **`frontend/src/contexts/AuthContext.tsx` & `backend/app/api/routes/auth.py`**:
   - Sliding-window proactive token renewal (`POST /api/v1/auth/refresh`) and silent 401 recovery.
5. **`frontend/src/components/PrintHiveLogo.tsx`**:
   - PrintHive dynamic hexagonal branding and theme colors.

---

## 4. Verification Checklist Before Marking Merge Complete

- [ ] All 31 multi-vendor tests pass:
  ```bash
  pytest backend/tests/unit/services/test_elegoo_client.py \
         backend/tests/unit/services/test_flashforge_client.py \
         backend/tests/unit/services/test_elegoo_upload.py \
         backend/tests/unit/test_scheduler_elegoo_bed_type.py \
         backend/tests/integration/test_elegoo_link_api.py \
         backend/tests/unit/services/baseline/test_elegoo_adapter.py
  ```
- [ ] Preflight UI tests pass:
  ```bash
  cd frontend && npx vitest run src/__tests__/components/AddPrinterPreflight.test.tsx src/__tests__/components/EditPrinterPreflight.test.tsx
  ```
- [ ] Frontend builds without errors:
  ```bash
  cd frontend && npm run build
  ```
- [ ] Build Docker image locally (`printhive:hardened`) and push/load to LXC 106.
- [ ] Live runtime check on LXC 106:
  - Both BambuLab (`192.168.1.113`) and Elegoo (`192.168.1.235`) report `connected: True`.
  - Watchdog does not log port 8883 errors or crash on Elegoo.
  - Browser UI shows custom models and camera/plate options in Add & Edit modals.
