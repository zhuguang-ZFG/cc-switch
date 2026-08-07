from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        converter._KEY_IN_FLIGHT.clear()

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

    def test_concurrent_leases_do_not_reuse_busy_key(self) -> None:
        keys = ["key-a", "key-b"]
        first = converter._lease_key("gpt-5.6-sol", keys, time.time())
        second = converter._lease_key("gpt-5.6-sol", keys, time.time())
        self.assertEqual(first, "key-a")
        self.assertEqual(second, "key-b")
        self.assertIsNone(converter._lease_key("gpt-5.6-sol", keys, time.time()))

    def test_release_makes_key_available_again(self) -> None:
        keys = ["key-a"]
        self.assertEqual(converter._lease_key("gpt-5.6-sol", keys, time.time()), "key-a")
        converter._release_key("key-a")
        self.assertEqual(converter._lease_key("gpt-5.6-sol", keys, time.time()), "key-a")

    def test_release_is_idempotent(self) -> None:
        converter._KEY_IN_FLIGHT["key-a"] = 1
        converter._release_key("key-a")
        converter._release_key("key-a")
        self.assertNotIn("key-a", converter._KEY_IN_FLIGHT)

    def test_nonstream_http_error_releases_lease(self) -> None:
        class Response:
            status_code = 402
            content = b'{"error":"limit"}'

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, *_args, **_kwargs):
                return Response()

        with patch.object(converter, "_custom_keys_for", return_value=["key-a"]), \
             patch.object(converter, "_validate_custom_url", return_value="https://example.invalid"), \
             patch.object(converter.httpx, "AsyncClient", return_value=Client()):
            with self.assertRaises(converter.HTTPException):
                asyncio.run(converter._chat_custom(
                    {"url": "https://example.invalid"}, {}, [], False, "gpt-5.6-sol", "rid"
                ))
        self.assertEqual(converter._KEY_IN_FLIGHT, {})

    def test_stream_generator_close_releases_lease(self) -> None:
        class Response:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def aiter_bytes(self):
                yield b"data: first\n\n"

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def stream(self, *_args, **_kwargs):
                return Response()

        async def scenario() -> None:
            stream = converter._stream_custom(
                "https://example.invalid", {}, {}, "gpt-5.6-sol", time.time(),
                "rid", time.time(), ["key-a"],
            )
            with patch.object(converter.httpx, "AsyncClient", return_value=Client()):
                self.assertEqual(await anext(stream), b"data: first\n\n")
                self.assertEqual(converter._KEY_IN_FLIGHT, {"key-a": 1})
                await stream.aclose()

        asyncio.run(scenario())
        self.assertEqual(converter._KEY_IN_FLIGHT, {})


if __name__ == "__main__":
    unittest.main()
