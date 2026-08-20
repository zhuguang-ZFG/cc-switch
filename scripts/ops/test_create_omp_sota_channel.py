from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("create_omp_sota_channel.py")
SPEC = importlib.util.spec_from_file_location("create_omp_sota_channel", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
sota = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sota)


class CreateOmpSotaChannelTests(unittest.TestCase):
    def test_payload_is_isolated_single_key_and_keeps_local_proxy(self):
        payload = sota.payload("opaque-key", 93)
        self.assertEqual(payload["id"], 93)
        # strict isolation (2026-08-20): the marked alias only, no base model
        self.assertEqual(payload["models"], "omp-sota-claude-opus-5")
        self.assertEqual(payload["test_model"], "omp-sota-claude-opus-5")
        self.assertEqual(
            json.loads(payload["model_mapping"]),
            {"omp-sota-claude-opus-5": "claude-opus-5"},
        )
        self.assertEqual(
            json.loads(payload["setting"])["proxy"], "http://127.0.0.1:7897"
        )
        self.assertNotIn("opaque-key", payload["setting"])

    def test_hydrate_key_recovers_masked_api_value_from_ssot(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE channels (id INTEGER, key TEXT)")
                connection.execute("INSERT INTO channels VALUES (93, 'actual-key')")
                connection.commit()
            finally:
                connection.close()
            hydrated = sota.hydrate_key(database, 93, {"id": 93, "key": "sk-***"})
            self.assertEqual(hydrated["key"], "actual-key")


if __name__ == "__main__":
    unittest.main()
