"""Tests for the redacted untrusted-provider conformance canary."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("probe_untrusted_openai_provider.py")
SPEC = importlib.util.spec_from_file_location("untrusted_provider_canary", MODULE_PATH)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canary
SPEC.loader.exec_module(canary)


class ParserTests(unittest.TestCase):
    def test_base_url_requires_https_and_rejects_userinfo(self):
        self.assertEqual(
            canary.validate_base_url("https://example.test/v1/"),
            "https://example.test",
        )
        for value in ("http://example.test", "https://user:pass@example.test"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canary.validate_base_url(value)

    def test_model_ids_reject_control_characters_and_excessive_length(self):
        self.assertEqual(canary.validate_model_id("provider/model-max"), "provider/model-max")
        for value in ("model\nforged", "x" * 129, "-leading-dash"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canary.validate_model_id(value)
        self.assertEqual(canary.safe_response_model("model\u001b[31m"), "[invalid-model-id]")

    def test_nonstream_sse_and_empty_output_are_detectable(self):
        result = canary.HttpResult(
            status=200,
            content_type="text/event-stream",
            body=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
            elapsed_ms=3,
        )
        parsed = canary.parse_response(result)
        self.assertEqual(parsed["wire_format"], "sse")
        self.assertEqual(parsed["text"], "")
        self.assertTrue(parsed["done"])

    def test_sse_extracts_model_text_usage_and_tool_arguments(self):
        body = b"\n".join(
            (
                b'data: {"model":"actual-model","choices":[{"delta":{"content":"OK"}}]}',
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"report_canary","arguments":"{\\\"value\\\":\\\"CANARY_TOOL_OK\\\"}"}}]}}]}',
                b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"prompt_tokens_details":{"cached_tokens":4}}}',
                b"data: [DONE]",
            )
        )
        parsed = canary.parse_sse_response(body)
        self.assertEqual(parsed["response_model"], "actual-model")
        self.assertEqual(parsed["text"], "OK")
        self.assertEqual(parsed["usage"]["cache_read_tokens"], 4)
        self.assertEqual(parsed["tool_calls"][0]["name"], "report_canary")
        self.assertTrue(parsed["done"])

    def test_alias_collapse_groups_multiple_requested_models(self):
        probes = [
            {
                "kind": "stream",
                "http_status": 200,
                "requested_model": requested,
                "response_model": "base-model",
            }
            for requested in ("base-model", "base-model-max", "base-model-xhigh")
        ]
        self.assertEqual(
            canary.alias_collapses(probes),
            [
                {
                    "response_model": "base-model",
                    "requested_models": [
                        "base-model",
                        "base-model-max",
                        "base-model-xhigh",
                    ],
                }
            ],
        )

    def test_wire_model_variants_detect_protocol_dependent_identity(self):
        probes = [
            {
                "kind": "nonstream",
                "requested_model": "model-A",
                "response_model": "claude-opus-5",
            },
            {
                "kind": "stream",
                "requested_model": "model-A",
                "response_model": "claude-opus-5-max",
            },
        ]
        self.assertEqual(
            canary.wire_model_variants(probes),
            [
                {
                    "requested_model": "model-A",
                    "response_models_by_wire": {
                        "nonstream": "claude-opus-5",
                        "stream": "claude-opus-5-max",
                    },
                }
            ],
        )

    def test_safe_json_rejects_secret_or_authorization_metadata(self):
        with self.assertRaisesRegex(RuntimeError, "secret"):
            canary.safe_json({"value": "top-secret"}, "top-secret")
        with self.assertRaisesRegex(RuntimeError, "authorization"):
            canary.safe_json({"Authorization": "redacted"})

    def test_dry_run_never_requires_or_prints_key(self):
        output = io.StringIO()
        with patch.dict(os.environ, {canary.DEFAULT_KEY_ENV: "test-secret"}, clear=False):
            with redirect_stdout(output):
                self.assertEqual(canary.main([]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["mode"], "dry-run")
        self.assertTrue(report["key_present"])
        self.assertNotIn("test-secret", output.getvalue())
        self.assertFalse(report["safety"]["mutates_provider"])


if __name__ == "__main__":
    unittest.main()
