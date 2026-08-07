from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SUPERVISOR = Path.home() / ".omp" / "guardian" / "proxies-supervisor.py"
spec = importlib.util.spec_from_file_location("proxies_supervisor", SUPERVISOR)
assert spec and spec.loader
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class SupervisorStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        supervisor.GUARDIAN_DIR = Path(self.tempdir.name)
        supervisor.STATUS_FILE = supervisor.GUARDIAN_DIR / "supervisor-status.json"

    def test_status_records_structured_service_health(self) -> None:
        services = {
            "codebuddy": supervisor.service_status(
                healthy=False,
                restart_blocked=True,
                last_error="restart limit reached",
                restarts_last_hour=5,
            ),
            "agentrouter": supervisor.service_status(
                healthy=True,
                restart_blocked=False,
                last_error=None,
                restarts_last_hour=0,
            ),
        }
        supervisor.write_status(services, {"codebuddy": 5}, "2026-08-08")
        payload = json.loads(supervisor.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["services"]["codebuddy"]["healthy"], False)
        self.assertEqual(payload["services"]["codebuddy"]["restartBlocked"], True)
        self.assertEqual(payload["services"]["codebuddy"]["lastError"], "restart limit reached")
        self.assertEqual(payload["services"]["codebuddy"]["restartsLastHour"], 5)
        self.assertEqual(payload["services"]["agentrouter"]["healthy"], True)

    def test_restart_count_does_not_consume_allowance(self) -> None:
        supervisor._restart_times["codebuddy"] = [supervisor.time.time() - 10]
        self.assertEqual(supervisor.restarts_last_hour("codebuddy"), 1)
        self.assertEqual(supervisor.restarts_last_hour("codebuddy"), 1)


if __name__ == "__main__":
    unittest.main()
