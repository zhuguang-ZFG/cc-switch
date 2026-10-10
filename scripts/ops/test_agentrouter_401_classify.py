#!/usr/bin/env python3
"""Invariant: agentrouter bridge must classify upstream 401 as keyside (dead token)
or fatal (fingerprint/WAF gate), never silently pass it through as an unclassified
error.

Why this exists (2026-10-10 00:2x incident): a dead pool key drew 401
`无效的令牌` from both gateways; the old classifier fell through 401 -> `fatal`,
so the key was never cooled, no key rotation happened, and the raw 401 surfaced
to OMP. 401 additionally sits in NewAPI's AutomaticDisableStatusCodes
(401,402,403,502), so leaking it upstream of the bridge risks auto-banning
ch180. Counter-case: agentrouter's fingerprint gate answers 401
`unauthorized client detected` for bare clients — key-unrelated; cooling keys
on it drains the healthy pool (UA-header fix belongs to the channel, not the
key pool), so it must stay fatal.

Run:  python -m unittest scripts.ops.test_agentrouter_401_classify
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("agentrouter-proxy.py")


def _load_bridge():
    for mod in ("fastapi", "httpx", "uvicorn"):
        try:
            __import__(mod)
        except ImportError:
            raise unittest.SkipTest(f"{sys.executable} lacks {mod}") from None
    spec = importlib.util.spec_from_file_location("agentrouter_proxy_under_test", SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Classify401Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _load_bridge()

    def _classify(self, status: int, text: str) -> str:
        return self.bridge._classify(status, text)

    def test_dead_token_401_is_keyside(self):
        for body in (
            "无效的令牌",
            '{"error":{"message":"Invalid token"}}',
            '{"error":{"message":"invalid api key"}}',
            '{"error":{"message":"api-key expired"}}',
        ):
            self.assertEqual(self._classify(401, body), "keyside", body)
            self.assertTrue(self.bridge._is_retryable(401, body), body)

    def test_fingerprint_gate_401_stays_fatal(self):
        for body in (
            "unauthorized client detected",
            "<html>Just a moment... Cloudflare challenge</html>",
        ):
            self.assertEqual(self._classify(401, body), "fatal", body)
            self.assertFalse(self.bridge._is_retryable(401, body), body)

    def test_unrecognized_401_fails_closed(self):
        self.assertEqual(self._classify(401, "something else"), "fatal")

    def test_preexisting_layers_unchanged(self):
        self.assertEqual(self._classify(400, "content[].thinking must be passed back"), "keyside")
        self.assertEqual(self._classify(400, "bad json"), "fatal")
        self.assertEqual(self._classify(402, "user quota is not enough"), "keyside")
        self.assertEqual(self._classify(403, "invalid key"), "fatal")
        self.assertEqual(self._classify(503, "try later"), "transient")


if __name__ == "__main__":
    unittest.main()
