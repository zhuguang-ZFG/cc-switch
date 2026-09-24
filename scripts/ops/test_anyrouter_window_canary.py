"""Inference health must follow the response body, including SSE terminal events."""
from __future__ import annotations

import http.client
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "anyrouter_window_canary", Path(__file__).with_name("anyrouter-window-canary.py")
)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


class Response(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status


def event(kind: str, **fields) -> bytes:
    payload = json.dumps({"type": kind, **fields})
    return f"event: {kind}\ndata: {payload}\n\n".encode()


def completed_stream(text: str = "OK", reason: str = "end_turn") -> bytes:
    return (
        event("message_start", message={"role": "assistant", "content": []})
        + event("content_block_start", index=0, content_block={"type": "text", "text": ""})
        + event("content_block_delta", index=0, delta={"type": "text_delta", "text": text})
        + event("content_block_stop", index=0)
        + event("message_delta", delta={"stop_reason": reason})
        + event("message_stop")
    )


class CanaryProbeTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.settings = Path(directory) / "settings.json"
        self.settings.write_text(json.dumps({"env": {
            "ANTHROPIC_BASE_URL": "https://anyrouter.top",
            "ANTHROPIC_AUTH_TOKEN": "fixture-only",
        }}), encoding="utf-8")
        self.enterContext(patch.object(canary, "CLAUDE_SETTINGS", self.settings))
        self.enterContext(patch.object(canary.time, "sleep"))
        self.urlopen = self.enterContext(patch.object(canary.urllib.request, "urlopen"))

    def direct_response(self, body: bytes, status: int = 200):
        self.urlopen.side_effect = lambda *args, **kwargs: Response(body, status)
        return canary.probe_opus_direct()

    def test_http_200_with_sse_error_is_closed(self):
        opened, state, detail = self.direct_response(event("error", error={"type": "overloaded_error"}))
        self.assertFalse(opened)
        self.assertEqual(state, "closed")
        self.assertIn("SSE error", detail)
        self.assertEqual(self.urlopen.call_count, canary.DIRECT_ATTEMPTS)

    def test_error_after_text_is_still_closed(self):
        body = completed_stream().split(b"event: message_delta")[0]
        body += event("error", error={"type": "overloaded_error"})
        self.assertFalse(self.direct_response(body)[0])

    def test_completed_text_opens_and_stops_retrying(self):
        for reason in ("end_turn", "max_tokens", "stop_sequence"):
            with self.subTest(reason=reason):
                self.urlopen.reset_mock()
                self.assertEqual(self.direct_response(completed_stream(reason=reason))[:2], (True, "open"))
                self.assertEqual(self.urlopen.call_count, 1)

    def test_empty_truncated_malformed_and_abnormal_streams_stay_closed(self):
        cases = [
            b"", b"<html>challenge</html>", b"data: not-json\n\n",
            completed_stream(text=" "),
            completed_stream(reason="error"),
            completed_stream().split(b"event: message_stop")[0],
            event("message_stop"),
            b"event: error\ndata: {}\n\n",
            b"data: []\n\n", b"data: \xff\n\n",
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertFalse(self.direct_response(body)[0])

    def test_stream_size_and_time_budgets_fail_closed(self):
        self.assertFalse(self.direct_response(b":" + b"x" * canary.MAX_RESPONSE_BYTES)[0])
        with patch.object(canary.time, "monotonic", side_effect=[0, canary.DIRECT_TIMEOUT + 1]):
            self.assertFalse(canary.validate_anthropic_stream(Response(completed_stream()))[0])

    def test_multiline_sse_data_and_crlf_are_supported(self):
        body = completed_stream().replace(
            b'"type": "message_start",', b'"type": "message_start",\ndata:'
        ).replace(b"\n", b"\r\n")
        self.assertTrue(self.direct_response(b": heartbeat\r\n\r\n" + body)[0])

    def test_read_failure_is_closed_and_bounded(self):
        response = self.urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read1.side_effect = http.client.IncompleteRead(b"partial")
        self.assertFalse(canary.probe_opus_direct()[0])
        self.assertEqual(self.urlopen.call_count, canary.DIRECT_ATTEMPTS)

    def test_takeover_skips_direct_network_request(self):
        self.settings.write_text(json.dumps({"env": {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:15721",
        }}), encoding="utf-8")
        self.assertEqual(canary.probe_opus_direct()[1], "skipped")
        self.urlopen.assert_not_called()

    def test_stream_split_inside_utf8_and_event_fields(self):
        body = completed_stream(text="好").replace(b"\\u597d", "好".encode())
        response = self.urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read1.side_effect = [bytes([byte]) for byte in body]
        self.assertTrue(canary.probe_opus_direct()[0])

    def test_non_200_success_status_is_not_healthy(self):
        self.assertFalse(self.direct_response(completed_stream(), status=204)[0])

    def test_bridge_requires_completed_anthropic_text(self):
        good = {"type": "message", "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}
        for payload, expected in [
            (good, True), ({**good, "content": []}, False),
            ({**good, "stop_reason": None}, False),
            ({"error": {"message": "overloaded"}}, False),
        ]:
            with self.subTest(payload=payload):
                self.urlopen.side_effect = lambda *a, **kw: Response(json.dumps(payload).encode())
                self.assertEqual(canary.probe_once(canary.OPUS_MODEL)[0], expected)

    def test_sol_requires_completed_chat_text(self):
        for choice, expected in [
            ({"message": {"content": "OK"}, "finish_reason": "stop"}, True),
            ({"message": {"content": ""}, "finish_reason": "stop"}, False),
            ({"message": {"content": "OK"}, "finish_reason": None}, False),
        ]:
            with self.subTest(choice=choice):
                self.urlopen.side_effect = lambda *a, **kw: Response(json.dumps({"choices": [choice]}).encode())
                self.assertEqual(canary.probe_sol()[0], expected)

    def test_json_rejects_html_oversize_and_wrong_shapes(self):
        for raw in (b"<html>challenge</html>", b"x" * (canary.MAX_RESPONSE_BYTES + 1), b"[]", b"null"):
            with self.subTest(raw=raw[:30]):
                self.assertFalse(canary.validate_json_response(Response(raw))[0])


class CanaryAlertStateTests(unittest.TestCase):
    def test_failed_delivery_retries_and_success_is_latched(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(canary, "STATE_FILE", Path(directory) / "state.json"),
            patch.object(canary, "load_secrets", return_value={}),
            patch.object(canary, "log"),
            patch.object(canary, "probe", return_value=(False, "closed")),
            patch.object(canary, "probe_sol", return_value=(False, "closed")),
            patch.object(canary, "probe_opus_direct", return_value=(True, "open", "completed SSE")),
            patch.object(canary, "send_telegram", side_effect=[False, True]) as send,
        ):
            canary.main()
            self.assertEqual(json.loads(canary.STATE_FILE.read_text())["opus_direct_state"], "closed")
            canary.main()
            self.assertEqual(json.loads(canary.STATE_FILE.read_text())["opus_direct_state"], "open")
            canary.main()
            self.assertEqual(send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
