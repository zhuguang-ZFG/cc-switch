from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("repair_guardian_channel_state.py")
SPEC = importlib.util.spec_from_file_location("repair_guardian_channel_state", PATH)
assert SPEC and SPEC.loader
repair = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repair
SPEC.loader.exec_module(repair)


class RepairGuardianChannelStateTests(unittest.TestCase):
    def test_removes_only_selected_channel_metadata(self) -> None:
        state = {
            "disabled_channels": [
                {"id": 48, "name": "old"},
                {"id": 7, "name": "keep"},
            ],
            "weight_history": {"48": {"weight": 20}, "7": {"weight": 5}},
            "degraded_channels": {"48": {"name": "old"}},
            "joined_channels": {"92": {"weight": 22}},
            "channel_identities": {"48": {"fingerprint": "old"}},
            "other": {"48": "not-owned"},
        }

        updated, removed = repair.remove_channel_state(state, {48, 92})

        self.assertEqual(removed, 5)
        self.assertEqual(updated["disabled_channels"], [{"id": 7, "name": "keep"}])
        self.assertEqual(updated["weight_history"], {"7": {"weight": 5}})
        self.assertEqual(updated["degraded_channels"], {})
        self.assertEqual(updated["joined_channels"], {})
        self.assertEqual(updated["channel_identities"], {})
        self.assertEqual(updated["other"], {"48": "not-owned"})
        self.assertIn("48", state["weight_history"])

    def test_restore_removes_file_that_did_not_exist_before(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = root / "state.json"
            created = root / "state.json.last-good"
            existing.write_bytes(b"original")
            created.write_bytes(b"partial")

            repair.restore_originals({existing: b"original", created: None})

            self.assertEqual(existing.read_bytes(), b"original")
            self.assertFalse(created.exists())


if __name__ == "__main__":
    unittest.main()
