from __future__ import annotations

import importlib.util
import json
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

    def test_requires_one_exact_review_tool_call(self):
        body = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": probe.REVIEW_TOOL_NAME,
                                    "arguments": json.dumps(
                                        {
                                            "severity": "none",
                                            "summary": probe.EXPECTED_REVIEW_TEXT,
                                        }
                                    ),
                                }
                            }
                        ]
                    }
                }
            ]
        }
        self.assertTrue(probe.review_tool_matches(body))
        body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "other"
        self.assertFalse(probe.review_tool_matches(body))
        self.assertFalse(probe.review_tool_matches({"choices": []}))

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

    def test_readiness_is_atomic_bounded_and_preserves_sibling_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            readiness = Path(temp) / "sota-readiness.json"
            probe.update_readiness(
                readiness,
                "zg-newapi/omp-sota-primary",
                75,
                "ready",
                "semantic-and-log-verified",
                checked_at_ms=1000,
            )
            probe.update_readiness(
                readiness,
                "zg-newapi/omp-sota-backup",
                86,
                "unavailable",
                "semantic-failed",
                checked_at_ms=2000,
            )
            payload = json.loads(readiness.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["ttlMs"], probe.DEFAULT_READINESS_TTL_MS)
            self.assertEqual(
                payload["candidates"]["zg-newapi/omp-sota-primary"]["status"],
                "ready",
            )
            self.assertEqual(
                payload["candidates"]["zg-newapi/omp-sota-backup"]["status"],
                "unavailable",
            )
            self.assertNotIn("prompt", readiness.read_text(encoding="utf-8"))
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_discovers_isolated_sota_channel_before_shared_pool(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER, name TEXT, status INTEGER, priority INTEGER, models TEXT)"
                )
                connection.executemany(
                    "INSERT INTO channels VALUES (?, ?, ?, ?, ?)",
                    [
                        (75, "tabitoken", 2, 50, "claude-opus-5,omp-sota-claude-opus-5"),
                        (93, "omp-sota-sotamodel", 2, 1, "claude-opus-5,omp-sota-claude-opus-5"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                probe.discover_channel_id(database, "omp-sota-claude-opus-5"),
                93,
            )


if __name__ == "__main__":
    unittest.main()
