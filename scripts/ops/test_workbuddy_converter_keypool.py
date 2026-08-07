from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

CONVERTER = Path.home() / ".kimi-code" / "proxies" / "codebuddy2openai" / "converter.py"
spec = importlib.util.spec_from_file_location("workbuddy_converter", CONVERTER)
assert spec and spec.loader
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


class WorkBuddyKeyPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        converter.KEY_STATE_PATH = Path(self.tempdir.name) / "key-state.json"
        converter._KEY_FAIL_AT.clear()
        converter._KEY_EXHAUSTED_UNTIL.clear()
        converter._KEY_ROUND.clear()

    def test_insufficient_balance_is_precisely_classified(self) -> None:
        self.assertTrue(converter._is_exhausted_key_error(401, '{"error":"Insufficient balance"}'))
        self.assertFalse(converter._is_exhausted_key_error(401, '{"error":"invalid api key"}'))
        self.assertFalse(converter._is_exhausted_key_error(403, "Insufficient balance"))

    def test_transient_cooldown_survives_new_request_epoch(self) -> None:
        keys = ["key-a", "key-b"]
        converter._mark_key_fail("gpt-5.6-sol", "key-a")
        later_request_epoch = time.time() + 1
        self.assertEqual(converter._pick_key("gpt-5.6-sol", keys, later_request_epoch), "key-b")

    def test_exhausted_key_is_persisted_without_plaintext(self) -> None:
        converter._mark_key_exhausted("gpt-5.6-sol", "secret-key-a")
        payload = json.loads(converter.KEY_STATE_PATH.read_text(encoding="utf-8"))
        serialized = json.dumps(payload)
        self.assertNotIn("secret-key-a", serialized)
        self.assertTrue(payload["exhausted"])

        converter._KEY_EXHAUSTED_UNTIL.clear()
        converter._load_key_state()
        self.assertEqual(
            converter._pick_key("gpt-5.6-sol", ["secret-key-a", "key-b"], time.time()),
            "key-b",
        )

    def test_success_clears_expired_persistent_quarantine(self) -> None:
        digest = converter._key_id("key-a")
        converter._KEY_EXHAUSTED_UNTIL[digest] = time.time() - 1
        converter._persist_key_state()
        converter._mark_key_success("key-a", time.time())
        payload = json.loads(converter.KEY_STATE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn(digest, payload["exhausted"])


if __name__ == "__main__":
    unittest.main()
