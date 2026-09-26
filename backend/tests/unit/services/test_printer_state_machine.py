"""Unit tests for the shared PrinterStateMachine and lifecycle handler."""

import pytest
from unittest.mock import MagicMock

from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_state_machine import PrinterStateMachine


class TestPrinterStateMachine:
    @pytest.fixture
    def mock_callbacks(self):
        return {
            "on_state_change": MagicMock(),
            "on_print_start": MagicMock(),
            "on_print_complete": MagicMock(),
            "on_print_running_observed": MagicMock(),
            "on_layer_change": MagicMock(),
            "on_bed_temp_update": MagicMock(),
            "on_finish_photo_moment": MagicMock(),
        }

    @pytest.fixture
    def sm(self, mock_callbacks):
        return PrinterStateMachine(
            serial_number="TEST_PRINTER_01",
            **mock_callbacks,
        )

    def test_record_dispatch_sets_subtask_id_and_file(self, sm):
        """record_dispatch must set last_dispatch_subtask_id and state fields."""
        subtask_id = sm.record_dispatch(filename="test_model.gcode")
        assert subtask_id is not None
        assert sm.last_dispatch_subtask_id == subtask_id
        assert sm.state.subtask_id == subtask_id
        assert sm.state.current_print == "test_model.gcode"
        assert sm.state.subtask_name == "test_model.gcode"
        assert sm.state.gcode_file == "test_model.gcode"

    def test_print_start_detection_on_prepare_or_running(self, sm, mock_callbacks):
        """Print start callback must fire with seconds-converted remaining_time and filename."""
        sm.record_dispatch(filename="plate_1.gcode")
        sm.state.remaining_time = 15  # 15 minutes

        # Transition from IDLE to PREPARE
        sm._previous_gcode_state = "IDLE"
        sm.process_transition(
            state_str="PREPARE",
            current_file="plate_1.gcode",
            stage_code=74,
        )

        assert sm.state.state == "PREPARE"
        assert sm.state.stg_cur == 74
        assert sm._was_running is True

        mock_callbacks["on_print_start"].assert_called_once()
        payload = mock_callbacks["on_print_start"].call_args[0][0]
        assert payload["filename"] == "plate_1.gcode"
        assert payload["subtask_name"] == "plate_1.gcode"
        # 15 minutes converted to 900 seconds
        assert payload["remaining_time"] == 900

        mock_callbacks["on_state_change"].assert_called()

    def test_transition_from_prepare_to_running_does_not_duplicate_print_start(self, sm, mock_callbacks):
        """Transitioning from PREPARE to RUNNING within the same job must not fire print_start twice."""
        sm.record_dispatch(filename="plate_1.gcode")
        sm._previous_gcode_state = "IDLE"
        sm.process_transition(state_str="PREPARE", current_file="plate_1.gcode")
        assert mock_callbacks["on_print_start"].call_count == 1

        # Now transitions to RUNNING
        sm.process_transition(state_str="RUNNING", current_file="plate_1.gcode")
        assert sm.state.state == "RUNNING"
        # Should not fire again
        assert mock_callbacks["on_print_start"].call_count == 1

    def test_running_first_observed_triggers_running_observed_callback(self, sm, mock_callbacks):
        """When server starts mid-print (RUNNING without previous state), trigger on_print_running_observed."""
        sm._previous_gcode_state = None  # fresh start
        sm.state.current_print = "in_flight.gcode"
        sm.state.remaining_time = 10

        sm.process_transition(state_str="RUNNING", current_file="in_flight.gcode")

        assert sm._was_running is True
        mock_callbacks["on_print_start"].assert_not_called()
        mock_callbacks["on_print_running_observed"].assert_called_once()
        payload = mock_callbacks["on_print_running_observed"].call_args[0][0]
        assert payload["filename"] == "in_flight.gcode"
        assert payload["remaining_time"] == 600

    def test_print_completion_preserves_filename_on_finish(self, sm, mock_callbacks):
        """On FINISH, trigger completion and photo moment, preserving filename for plate clear."""
        sm.record_dispatch(filename="completed_model.gcode")
        sm._previous_gcode_state = "RUNNING"
        sm._was_running = True

        sm.process_transition(state_str="FINISH", current_file="completed_model.gcode")

        assert sm.state.state == "FINISH"
        mock_callbacks["on_print_complete"].assert_called_once()
        comp_payload = mock_callbacks["on_print_complete"].call_args[0][0]
        assert comp_payload["status"] == "completed"
        assert comp_payload["filename"] == "completed_model.gcode"

        mock_callbacks["on_finish_photo_moment"].assert_called_once()

        # Crucial for plate-clear confirmation gate: filename must NOT be wiped on FINISH
        assert sm.state.current_print == "completed_model.gcode"
        assert sm.state.subtask_name == "completed_model.gcode"

    def test_print_failure_triggers_failed_and_cleans_up(self, sm, mock_callbacks):
        """On FAILED, trigger completion with failed status and clean up job state."""
        sm.record_dispatch(filename="failed_model.gcode")
        sm._previous_gcode_state = "RUNNING"
        sm._was_running = True

        sm.process_transition(state_str="FAILED")

        assert sm.state.state == "FAILED"
        mock_callbacks["on_print_complete"].assert_called_once()
        assert mock_callbacks["on_print_complete"].call_args[0][0]["status"] == "failed"
        mock_callbacks["on_finish_photo_moment"].assert_not_called()

        # Job state cleaned up
        assert sm.state.current_print == ""
        assert sm.state.subtask_name == ""

    def test_user_abort_triggers_completion_with_aborted(self, sm, mock_callbacks):
        """Transition from RUNNING directly to IDLE triggers aborted completion."""
        sm.record_dispatch(filename="aborted_model.gcode")
        sm._previous_gcode_state = "RUNNING"
        sm._was_running = True

        sm.process_transition(state_str="IDLE")

        mock_callbacks["on_print_complete"].assert_called_once()
        assert mock_callbacks["on_print_complete"].call_args[0][0]["status"] == "aborted"

    def test_layer_change_callback_fires(self, sm, mock_callbacks):
        """on_layer_change should fire when layer_num changes."""
        sm.state.layer_num = 1
        sm.process_transition(state_str="RUNNING")
        mock_callbacks["on_layer_change"].assert_called_once_with(1)

        sm.process_transition(state_str="RUNNING")
        assert mock_callbacks["on_layer_change"].call_count == 1

        sm.state.layer_num = 2
        sm.process_transition(state_str="RUNNING")
        assert mock_callbacks["on_layer_change"].call_count == 2
        mock_callbacks["on_layer_change"].assert_called_with(2)

    def test_bed_temp_update_callback_fires(self, sm, mock_callbacks):
        """on_bed_temp_update fires when bed temp delta exceeds 0.1C."""
        sm.state.temperatures = {"bed": 60.5}
        sm.process_transition(state_str="RUNNING")
        mock_callbacks["on_bed_temp_update"].assert_called_once_with(60.5)

        # Same temp: no duplicate
        sm.process_transition(state_str="RUNNING")
        assert mock_callbacks["on_bed_temp_update"].call_count == 1

        # Changed temp
        sm.state.temperatures = {"bed": 62.0}
        sm.process_transition(state_str="RUNNING")
        assert mock_callbacks["on_bed_temp_update"].call_count == 2
        mock_callbacks["on_bed_temp_update"].assert_called_with(62.0)
