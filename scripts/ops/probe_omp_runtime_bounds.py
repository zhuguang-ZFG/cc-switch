"""Exercise native OMP fallback and streaming deadlines against a local fixture.

Uses a temporary OMP home and dummy credentials. No live model is called and no
installed configuration is changed. Run explicitly; not part of unit discovery.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml


def main() -> int:
    executable = shutil.which("omp")
    if not executable:
        raise RuntimeError("OMP executable unavailable")
    observed = []
    mode = "fallback"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = request.get("model")
            observed.append({"model": model, "stream": request.get("stream")})
            try:
                if mode == "first-event":
                    time.sleep(5)
                    self.send_response(504)
                    self.end_headers()
                    return
                if mode == "fallback" and model == "primary":
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"fixture unavailable","type":"server_error"}}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def chunk(delta, finish=None):
                    value = {"id": "fixture-response", "object": "chat.completion.chunk",
                             "created": int(time.time()), "model": model,
                             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()

                chunk({"role": "assistant", "content": "BOUNDS_OK"})
                if mode == "idle":
                    time.sleep(5)
                    return
                chunk({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="omp-runtime-bounds-") as directory:
            root = Path(directory)
            env = dict(os.environ, PI_CODING_AGENT_DIR=str(root), NO_PROXY="127.0.0.1,localhost")
            env.pop("HTTP_PROXY", None)
            env.pop("HTTPS_PROXY", None)
            models = {"providers": {"stability-fixture": {
                "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                "apiKey": "local-fixture-only", "api": "openai-completions",
                "models": [{"id": name, "name": name, "reasoning": False,
                            "input": ["text"], "contextWindow": 32768, "maxTokens": 512}
                           for name in ("primary", "fallback")],
            }}}
            (root / "models.yml").write_text(yaml.safe_dump(models), encoding="utf-8")
            for mode in ("fallback", "first-event", "idle"):
                observed.clear()
                config = {"modelRoles": {"default": "stability-fixture/primary"},
                          "providers": {"streamFirstEventTimeoutSeconds": 1, "streamIdleTimeoutSeconds": 1},
                          "retry": {"maxRetries": 1 if mode == "fallback" else 0,
                                    "baseDelayMs": 10, "maxDelayMs": 100,
                                    "modelFallback": mode == "fallback",
                                    "fallbackChains": {"stability-fixture/primary": ["stability-fixture/fallback"]}}}
                (root / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
                started = time.monotonic()
                result = subprocess.run(
                    [executable, "-p", "Reply BOUNDS_OK", "--model", "stability-fixture/primary",
                     "--no-tools", "--no-extensions", "--no-skills", "--no-session", "--no-title",
                     "--max-time", "15s"],
                    cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=25,
                )
                elapsed = time.monotonic() - started
                models_seen = [item["model"] for item in observed]
                if mode == "fallback":
                    ok = result.returncode == 0 and "BOUNDS_OK" in result.stdout and "fallback" in models_seen
                else:
                    diagnostic = result.stdout + result.stderr
                    ok = result.returncode != 0 and bool(observed) and elapsed < 12 and any(
                        word in diagnostic.lower() for word in ("timeout", "timed out", "stream stalled"))
                summary = {"scenario": mode, "ok": ok, "seconds": round(elapsed, 2),
                           "exit_code": result.returncode, "requests": observed.copy()}
                results.append(summary)
                print(json.dumps(summary), flush=True)
    finally:
        server.shutdown()
        server.server_close()
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
