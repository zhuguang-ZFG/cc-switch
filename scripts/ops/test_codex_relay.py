import http.client
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("codex-relay.py")
SPEC = importlib.util.spec_from_file_location("codex_relay", MODULE_PATH)
relay = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(relay)
relay.KEY = "test-key"
relay.INSTALL_ID = "test-installation-id"


def completed_sse(text="PONG"):
    events = [
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 3, "output_tokens": 1}}},
    ]
    payload = b"".join(
        f"data: {json.dumps(event)}\n\n".encode("utf-8") for event in events
    )
    return io.BytesIO(payload)


def failed_sse(message="upstream rejected request"):
    event = {
        "type": "response.failed",
        "response": {"error": {"message": message}},
    }
    return io.BytesIO(f"data: {json.dumps(event)}\n\n".encode("utf-8"))


def partial_then_failed_sse(message="upstream rejected request"):
    events = [
        {"type": "response.output_text.delta", "delta": "partial"},
        {"type": "response.failed", "response": {"error": {"message": message}}},
    ]
    return io.BytesIO(b"".join(
        f"data: {json.dumps(event)}\n\n".encode("utf-8") for event in events
    ))


class RelayUnitTests(unittest.TestCase):
    def test_build_body_strips_captured_runtime_context(self):
        body, _ = relay.build_body(
            "gpt-5.6-sol",
            [
                {"role": "system", "content": "CURRENT_CALLER_MARKER"},
                {"role": "user", "content": "continue"},
            ],
        )

        serialized = json.dumps(body)
        self.assertIn("CURRENT_CALLER_MARKER", body["instructions"])
        self.assertEqual([item["role"] for item in body["input"]], ["user"])
        self.assertNotIn("sandbox_mode", serialized)
        self.assertNotIn("Approval policy is currently never", serialized)
        self.assertNotIn("D:\\\\Users\\\\cc-switch", serialized)
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)
        self.assertNotIn("parallel_tool_calls", body)

    def test_build_body_preserves_instructions_history_and_tools(self):
        body, _ = relay.build_body("gpt-5.5", [
            {"role": "system", "content": "Follow caller policy."},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "tool", "tool_call_id": "call-1", "content": "third"},
            {"role": "user", "content": "fourth"},
        ], [{
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up a value",
                "parameters": {"type": "object", "properties": {}},
            },
        }], {
            "type": "function",
            "function": {"name": "lookup"},
        }, False)

        self.assertIn("Follow caller policy.", body["instructions"])
        prompt = body["input"][-1]["content"][0]["text"]
        self.assertIn("[USER]\nfirst", prompt)
        self.assertIn("[ASSISTANT]\nsecond", prompt)
        self.assertIn("[TOOL RESULT call-1]\nthird", prompt)
        self.assertIn("[USER]\nfourth", prompt)
        self.assertEqual(body["tools"][0]["name"], "lookup")
        self.assertEqual(body["tool_choice"], {"type": "function", "name": "lookup"})
        self.assertFalse(body["parallel_tool_calls"])

    def test_port_argument_is_honored(self):
        args = relay.parse_args(["--port", "16001", "--log-file", "relay.log"])
        self.assertEqual(args.port, 16001)
        self.assertEqual(args.log_file, "relay.log")


class RelayHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = relay.BoundedThreadingHTTPServer(("127.0.0.1", 0), relay.Handler, max_workers=2)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request("POST", "/v1/chat/completions", json.dumps(body), {
            "Content-Type": "application/json",
        })
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def test_non_stream_response_and_usage(self):
        with patch.object(relay, "call_upstream", return_value=completed_sse()):
            response, data = self.request({"model": "gpt-5.5", "messages": [{"role": "user", "content": "ping"}]})

        payload = json.loads(data)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Connection"), "close")
        self.assertEqual(payload["choices"][0]["message"]["content"], "PONG")
        self.assertEqual(payload["usage"]["total_tokens"], 4)

    def test_stream_has_valid_headers_and_done_frame(self):
        with patch.object(relay, "call_upstream", return_value=completed_sse()):
            response, data = self.request({
                "model": "gpt-5.5",
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            })

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertIn(b'"content": "PONG"', data)
        self.assertTrue(data.endswith(b"data: [DONE]\n\n"))

    def test_upstream_failure_is_not_returned_as_http_200(self):
        with patch.object(relay, "call_upstream", side_effect=relay.RelayError(502, "upstream failed")):
            response, data = self.request({"model": "gpt-5.5", "messages": []})

        self.assertEqual(response.status, 502)
        self.assertEqual(json.loads(data)["error"]["type"], "relay_error")

    def test_stream_failure_before_semantic_event_is_http_error(self):
        with patch.object(relay, "call_upstream", return_value=failed_sse()):
            response, data = self.request({
                "model": "gpt-5.5",
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            })

        self.assertEqual(response.status, 502)
        self.assertEqual(json.loads(data)["error"]["type"], "upstream_error")

    def test_stream_failure_after_semantic_event_has_one_error_terminal(self):
        with patch.object(relay, "call_upstream", return_value=partial_then_failed_sse()):
            response, data = self.request({
                "model": "gpt-5.5",
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            })

        self.assertEqual(response.status, 200)
        self.assertIn(b'"type": "upstream_error"', data)
        self.assertNotIn(b'"finish_reason": "stop"', data)
        self.assertEqual(data.count(b"data: [DONE]"), 1)


if __name__ == "__main__":
    unittest.main()
