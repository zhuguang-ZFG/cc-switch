import errno
import http.client
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
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


class SocketStalledUpstream:
    """Fake upstream backed by a real socketpair so select-driven polling works.

    Mirrors the http.client response layout (fp.raw._sock). A background
    thread delivers the SSE payload only after *delay* seconds; before that
    the socket is not readable, so the relay's select poll fires instead of
    the read blocking.
    """

    def __init__(self, payload, delay):
        self._recv, self._send = socket.socketpair()
        self._payload = payload
        self._delay = delay
        self.fp = _FakeResponseFP(self._recv)
        threading.Thread(target=self._deliver, daemon=True).start()

    def _deliver(self):
        time.sleep(self._delay)
        try:
            self._send.sendall(self._payload)
        except OSError:
            pass
        finally:
            try:
                self._send.close()
            except OSError:
                pass

    def read(self, _size):
        return self._payload

    def close(self):
        for sock in (self._recv, self._send):
            try:
                sock.close()
            except OSError:
                pass


class _FakeResponseFP:
    def __init__(self, sock):
        self.raw = _FakeResponseRaw(sock)


class _FakeResponseRaw:
    def __init__(self, sock):
        self._sock = sock


class RelayUnitTests(unittest.TestCase):
    def test_fingerprint_matches_current_codex_cli(self):
        headers = relay.fingerprint_headers("test-session")
        self.assertEqual(headers["Originator"], "codex_cli_rs")
        self.assertEqual(headers["Version"], "0.146.0")
        self.assertIn("codex_exec/0.146.0", headers["User-Agent"])

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
        args = relay.parse_args([
            "--port", "16001",
            "--log-file", "relay.log",
            "--upstream", "https://example.test/codex/v1/responses",
            "--secret-name", "sharedchat_codex_key",
        ])
        self.assertEqual(args.port, 16001)
        self.assertEqual(args.log_file, "relay.log")
        self.assertEqual(args.upstream, "https://example.test/codex/v1/responses")
        self.assertEqual(args.secret_name, "sharedchat_codex_key")

    def test_load_key_uses_selected_secret_name(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets_file = Path(directory) / "secrets.json"
            secrets_file.write_text(json.dumps({
                "zzzcoding_codex_key": "old-key",
                "sharedchat_codex_key": "selected-key",
            }), encoding="utf-8")
            with patch.object(relay, "SECRETS_FILE", secrets_file), patch.dict(
                os.environ,
                {
                    "CODEX_RELAY_KEY": "wrong-global-key",
                    "CODEX_RELAY_KEY_SHAREDCHAT_CODEX_KEY": "",
                },
            ):
                self.assertEqual(relay.load_key("sharedchat_codex_key"), "selected-key")

    def test_load_key_uses_scoped_environment_override(self):
        with patch.dict(
            os.environ,
            {"CODEX_RELAY_KEY_SHAREDCHAT_CODEX_KEY": "selected-env-key"},
        ):
            self.assertEqual(relay.load_key("sharedchat_codex_key"), "selected-env-key")

    def test_configure_runtime_rejects_non_https_upstream(self):
        with self.assertRaisesRegex(SystemExit, "HTTPS URL"):
            relay.configure_runtime("http://example.test/responses", "sharedchat_codex_key")

    def test_scheduled_task_defaults_to_windowless_python(self):
        source = Path(__file__).with_name("register-codex-relay-task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(r"python313\current\pythonw.exe", source)
        self.assertIn("(Get-Command pythonw -ErrorAction Stop).Source", source)
        self.assertNotIn(r"python313\current\python.exe", source)
        self.assertNotIn("(Get-Command python -ErrorAction Stop).Source", source)

    def test_scheduled_task_deployment_is_isolated_and_rollback_capable(self):
        source = Path(__file__).with_name("register-codex-relay-task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('"codex-relay-$Port"', source)
        self.assertIn("Export-ScheduledTask", source)
        self.assertIn("Get-RelayListenerOwner", source)
        self.assertIn("OwningProcess", source)
        self.assertIn("$existingTaskXml", source)
        self.assertIn("-m py_compile $sourceRelay", source)
        self.assertIn("$env:PYTHONPYCACHEPREFIX = $validationCache", source)
        self.assertLess(source.index("$upstreamUri = [Uri]$Upstream"), source.index("$stagedRelay"))


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

    def test_stalled_read_hits_semantic_timeout_not_urlopen_fallback(self):
        upstream = SocketStalledUpstream(b"", delay=3600)
        with patch.object(relay, "call_upstream", return_value=upstream), \
                patch.object(relay, "SEMANTIC_TIMEOUT", -1), \
                patch.object(relay, "READ_POLL_TIMEOUT", 0.02):
            started = time.monotonic()
            response, data = self.request({
                "model": "gpt-5.5",
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            })
            elapsed = time.monotonic() - started

        self.assertEqual(response.status, 504)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["type"], "timeout_error")
        # semantic-timeout branch, not the urlopen TimeoutError fallback
        self.assertIn("no semantic output", payload["error"]["message"])
        self.assertLess(elapsed, 5)

    def test_stalled_read_recovers_before_semantic_deadline(self):
        upstream = SocketStalledUpstream(completed_sse().getvalue(), delay=0.3)
        with patch.object(relay, "call_upstream", return_value=upstream), \
                patch.object(relay, "READ_POLL_TIMEOUT", 0.05):
            response, data = self.request({
                "model": "gpt-5.5",
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            })

        self.assertEqual(response.status, 200)
        self.assertIn(b'"content": "PONG"', data)
        self.assertTrue(data.endswith(b"data: [DONE]\n\n"))


class RelayListenerExclusivityTests(unittest.TestCase):
    """A second relay on a live port must fail loudly, not shadow-bind.

    Windows SO_REUSEADDR (HTTPServer's default) lets a second process bind a
    port that is already LISTENing, so duplicate relays split traffic
    non-deterministically instead of the loser exiting.
    """

    def test_address_reuse_is_disabled_only_on_windows(self):
        self.assertEqual(
            relay.BoundedThreadingHTTPServer.allow_reuse_address,
            sys.platform != "win32",
        )

    def test_second_bind_on_live_port_is_refused(self):
        first = relay.BoundedThreadingHTTPServer(("127.0.0.1", 0), relay.Handler, max_workers=1)
        thread = threading.Thread(target=first.serve_forever, daemon=True)
        thread.start()
        port = first.server_address[1]
        try:
            with self.assertRaises(OSError) as caught:
                relay.BoundedThreadingHTTPServer(("127.0.0.1", port), relay.Handler, max_workers=1)
            self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
        finally:
            first.shutdown()
            first.server_close()
            thread.join(timeout=2)

    def test_rebind_after_shutdown_succeeds(self):
        """Exclusive binding must not break supervisor kill-then-restart."""
        first = relay.BoundedThreadingHTTPServer(("127.0.0.1", 0), relay.Handler, max_workers=1)
        thread = threading.Thread(target=first.serve_forever, daemon=True)
        thread.start()
        port = first.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/healthz")
        connection.getresponse().read()
        connection.close()
        first.shutdown()
        first.server_close()
        thread.join(timeout=2)

        second = relay.BoundedThreadingHTTPServer(("127.0.0.1", port), relay.Handler, max_workers=1)
        second.server_close()


if __name__ == "__main__":
    unittest.main()
