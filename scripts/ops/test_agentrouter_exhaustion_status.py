#!/usr/bin/env python3
"""Live-bridge invariant: agentrouter-proxy retry-exhaustion must answer 503.

Why this exists (2026-10-08 20:09 incident): the bridge re-raised the *last
upstream status* when its key×gateway retry budget ran out. After a keyside
400 storm OMP saw a bare `400 openai_error` (capture
`~/.omp/logs/http-400-requests/1791461385299-3q6plsb657reb.json`) and the
backup legs never ran.

NewAPI failover semantics are pinned by `scripts/ops/newapi-local-smoke.py`
REQUIRED_OPTIONS (operator contract, corroborated locally):
  AutomaticRetryStatusCodes   = 408,500-503    -> only these trigger failover
  AutomaticDisableStatusCodes = 401,402,403,502 -> these auto-disable a channel

Therefore exhaustion must surface as 503: it is retried (failover to ch118/ch15)
and it never auto-disables ch180. A 4xx (keyside 400) suppresses failover; the
`"no keys"` default 502 additionally risks auto-disabling the main leg.

Runs the *live* script in an isolated copy with a stub upstream that always
answers a keyside 400, so the retry budget is provably exhausted. No production
traffic and no production config is touched.

Run:  python -m unittest scripts.ops.test_agentrouter_exhaustion_status
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE = Path("C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py")
BRIDGE_PORT = 8798
STUB_PORT = 8799
LOCAL_KEY = "local-test-key"
EXPECTED_STATUS = 503
KEYSIDE_BODY = {"error": {"message": "content[].thinking must be passed back"}}


class _StubUpstream(http.server.BaseHTTPRequestHandler):
    """Deterministic keyside-classified 400 -> every bridge attempt fails."""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        body = json.dumps(KEYSIDE_BODY).encode()
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Silence stub request logging."""


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


class ExhaustionStatusTests(unittest.TestCase):
    work: tempfile.TemporaryDirectory
    root: Path
    stub: http.server.ThreadingHTTPServer

    @classmethod
    def setUpClass(cls) -> None:
        if not BRIDGE.exists():
            raise unittest.SkipTest(f"bridge not present at {BRIDGE}")
        for mod in ("fastapi", "httpx", "uvicorn"):
            try:
                __import__(mod)
            except ImportError:  # pragma: no cover - env dependent
                raise unittest.SkipTest(f"{sys.executable} lacks {mod}") from None
        cls.work = tempfile.TemporaryDirectory(prefix="ar-exhaust-")
        cls.root = Path(cls.work.name)
        shutil.copy2(BRIDGE, cls.root / "agentrouter-proxy.py")
        for port in (BRIDGE_PORT, STUB_PORT):
            if _port_in_use(port):
                cls.work.cleanup()
                raise unittest.SkipTest(f"port {port} already in use; isolation impossible")
        cls.stub = http.server.ThreadingHTTPServer(("127.0.0.1", STUB_PORT), _StubUpstream)
        threading.Thread(target=cls.stub.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stub.shutdown()
        cls.stub.server_close()
        cls.work.cleanup()

    def _post_exhausted_request(self, keys: list[str], tag: str) -> tuple[int, str]:
        (self.root / "keys.json").write_text(json.dumps({"keys": keys}), encoding="utf-8")
        env = {
            **os.environ,
            "AIR_OUTER_BASE": f"http://127.0.0.1:{STUB_PORT}/v1",
            "AGENTROUTER_BASE": f"http://127.0.0.1:{STUB_PORT}/v1",
            "KEY_COOLDOWN_S": "180",
        }
        proc = subprocess.Popen(
            [sys.executable, str(self.root / "agentrouter-proxy.py"), "--host", "127.0.0.1",
             "--port", str(BRIDGE_PORT), "--api-key", LOCAL_KEY,
             "--log", str(self.root / f"bridge-{tag}.log")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=str(self.root),
        )
        try:
            self.assertTrue(_wait_port(BRIDGE_PORT), f"bridge copy did not start ({tag})")
            body = json.dumps({"model": "deepseek-v4-flash",
                               "messages": [{"role": "user", "content": "hi"}], "stream": False}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{BRIDGE_PORT}/v1/chat/completions", data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + LOCAL_KEY})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, r.read()[:200].decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read()[:200].decode("utf-8", "replace")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_keyside_storm_exhaustion_is_503(self):
        """keyside 400 风暴吃掉全部 key 后，客户端拿到的必须是可 failover 的 503。"""
        status, body = self._post_exhausted_request(["dummy-key-1"], "keyside")
        self.assertEqual(status, EXPECTED_STATUS,
                         f"exhaustion must be {EXPECTED_STATUS} (retry set), got {status}: {body}")

    def test_empty_keypool_is_not_502(self):
        """key 池全冷却走 `no keys` 分支：502 落自动禁用集，必须同样 503。"""
        status, body = self._post_exhausted_request([], "nokeys")
        self.assertEqual(status, EXPECTED_STATUS,
                         f"empty key pool must be {EXPECTED_STATUS}, got {status}: {body}")
        self.assertNotEqual(status, 502, "502 would auto-disable ch180 (AutomaticDisableStatusCodes)")


if __name__ == "__main__":
    unittest.main()
