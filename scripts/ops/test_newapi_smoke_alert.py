import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PATH = Path(__file__).with_name("newapi-smoke-alert.py")
SPEC = importlib.util.spec_from_file_location("newapi_smoke_alert", PATH)
alert = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = alert
SPEC.loader.exec_module(alert)


class SmokeAlertTests(unittest.TestCase):
    def test_failed_transition_alerts_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            with (
                patch.object(alert, "STATE_FILE", state),
                patch.object(alert, "latest_summary", return_value="summary"),
                patch.object(alert, "send_alert", return_value=True) as send,
                patch.object(sys, "argv", ["alert", "--exit-code", "1"]),
            ):
                self.assertEqual(alert.main(), 0)
                self.assertEqual(alert.main(), 0)
            send.assert_called_once_with(True, "summary")

    def test_delivery_failure_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            with (
                patch.object(alert, "STATE_FILE", state),
                patch.object(alert, "latest_summary", return_value="summary"),
                patch.object(alert, "send_alert", return_value=False) as send,
                patch.object(sys, "argv", ["alert", "--exit-code", "1"]),
            ):
                self.assertEqual(alert.main(), 1)
                self.assertEqual(alert.main(), 1)
            self.assertEqual(send.call_count, 2)
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
