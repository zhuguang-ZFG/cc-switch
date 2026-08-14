"""Unit tests for process-ownership checks in system-health-check.py."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parent / "system-health-check.py"
SPEC = importlib.util.spec_from_file_location("system_health_check", MODULE_PATH)
assert SPEC and SPEC.loader
health = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = health
SPEC.loader.exec_module(health)


class RelayOwnerTests(unittest.TestCase):
    def test_exact_process_and_listener_owner_is_healthy(self) -> None:
        processes = [
            {
                "ProcessId": 101,
                "CommandLine": r"python C:\runtime\codex-relay.py --port 15999",
            },
            {
                "ProcessId": 102,
                "CommandLine": r"python C:\runtime\codex-relay.py --port 16000",
            },
        ]
        listeners = [
            {"LocalPort": 15999, "OwningProcess": 101},
            {"LocalPort": 16000, "OwningProcess": 102},
        ]

        self.assertEqual(health.relay_owner_violations(processes, listeners), [])

    def test_duplicate_relay_and_unrelated_listener_are_rejected(self) -> None:
        processes = [
            {
                "ProcessId": 101,
                "CommandLine": r"python C:\a\codex-relay.py --port 15999",
            },
            {
                "ProcessId": 103,
                "CommandLine": r"python C:\b\codex-relay.py --port 15999",
            },
            {
                "ProcessId": 102,
                "CommandLine": r"python C:\runtime\codex-relay.py --port 16000",
            },
        ]
        listeners = [
            {"LocalPort": 15999, "OwningProcess": 101},
            {"LocalPort": 16000, "OwningProcess": 999},
        ]

        self.assertEqual(
            health.relay_owner_violations(processes, listeners),
            [
                "port 15999: relay_processes=2 expected=1",
                "port 16000: process_pid=102 listener_pids=[999]",
            ],
        )

    def test_similar_script_name_does_not_match(self) -> None:
        processes = [
            {
                "ProcessId": 101,
                "CommandLine": r"python C:\runtime\not-codex-relay.py --port 15999",
            },
            {
                "ProcessId": 102,
                "CommandLine": r"python C:\runtime\codex-relay.py --port 16000",
            },
        ]
        listeners = [
            {"LocalPort": 15999, "OwningProcess": 101},
            {"LocalPort": 16000, "OwningProcess": 102},
        ]

        self.assertEqual(
            health.relay_owner_violations(processes, listeners),
            ["port 15999: relay_processes=0 expected=1"],
        )


if __name__ == "__main__":
    unittest.main()
