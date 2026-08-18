from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("probe_omp_sota_alias.py")
SPEC = importlib.util.spec_from_file_location("probe_omp_sota_alias", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class OmpSotaProbeTests(unittest.TestCase):
    def test_extracts_only_expected_chat_content(self):
        body = {"choices": [{"message": {"content": " OMP-SOTA-OK "}}]}
        self.assertEqual(probe.extract_text(body), "OMP-SOTA-OK")
        self.assertEqual(probe.extract_text({"error": "secret"}), "")
        self.assertTrue(probe.semantic_matches("OMP-SOTA-OK"))
        self.assertTrue(probe.semantic_matches("OMP-SOTA-OK."))
        self.assertFalse(probe.semantic_matches("OMP-SOTA-OK extra"))

    def test_reads_fresh_marked_log_and_validates_posture(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE logs (id INTEGER, channel_id INTEGER, model_name TEXT, "
                    "is_stream INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, use_time INTEGER)"
                )
                connection.execute(
                    "INSERT INTO logs VALUES (2, 75, 'omp-sota-claude-opus-5', 0, 10, 2, 1)"
                )
                connection.commit()
            finally:
                connection.close()
            row = probe.latest_log_after(
                database, 1, "omp-sota-claude-opus-5", wait_seconds=0.1
            )
            self.assertTrue(probe.verify_log(row, 75, "omp-sota-claude-opus-5"))
            self.assertFalse(probe.verify_log(row, 76, "omp-sota-claude-opus-5"))


if __name__ == "__main__":
    unittest.main()
