"""Exercise native OMP + real GitNexus MCP against a local model fixture.

Uses dummy credentials, a temporary repository, and real subprocess test evidence.
Requires the cc-switch GitNexus index; never changes production configuration.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml


def main() -> int:
    observed, errors = [], []
    calls = [
        ("write", {"path": "subject.js", "content": "GOOD\n"}),
        ("task_verify", {"action": "run"}),
        ("write", {"path": "subject.js", "content": "BAD\n"}),
        ("task_verify", {"action": "status"}),
        ("task_verify", {"action": "run"}),
        ("write", {"path": "unknown.js", "content": "uncovered\n"}),
        ("write", {"path": "subject.js", "content": "GOOD AGAIN\n"}),
        ("task_verify", {"action": "run"}),
        ("list_repos", {}),
        ("context", {"repo": "cc-switch", "uid": "Function:scripts/ops/omp-model-routing-observability.js:acquireCanaryLease"}),
        ("impact", {"repo": "cc-switch", "target": "acquireCanaryLease", "direction": "upstream", "maxDepth": 2}),
        ("write", {"path": "subject.js", "content": "GOOD AUTOMATIC\n"}),
    ]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(request)
            step = len(observed) - 1
            if step < len(calls):
                wanted, args = calls[step]
                names = [t["function"]["name"] for t in request.get("tools", [])]
                name = next((n for n in names if n in (wanted, f"mcp__gitnexus_{wanted}")), None)
                if name is None:
                    text = json.dumps(request.get("messages", []))
                    paths = re.findall(r"xd://[^\s`\"\\]+", text)
                    path = next((p for p in paths if p.rstrip("/").split("/")[-1] in (wanted, f"mcp__gitnexus_{wanted}")), None)
                    if path:
                        name, args = "write", {"path": path, "content": json.dumps(args)}
                if name is None:
                    errors.append({"missing": wanted, "tools": names, "paths": paths})
                    delta, finish = {"role": "assistant", "content": "FIXTURE_TOOL_MISSING"}, "stop"
                else:
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"call_{step}",
                        "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
                    finish = "tool_calls"
            else:
                delta, finish = {"role": "assistant", "content": "FIXTURE_COMPLETE"}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for body, reason in ((delta, None), ({}, finish)):
                chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": int(time.time()),
                         "model": "fixture", "choices": [{"index": 0, "delta": body, "finish_reason": reason}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="omp-verification-native-") as directory:
            base = Path(directory)
            root, agent = base / "repo", base / "agent"
            root.mkdir()
            extensions = agent / "extensions"
            extensions.mkdir(parents=True)
            source = Path(__file__).resolve().parent
            for name in ("omp-task-verification.js", "omp-model-routing-observability.js"):
                shutil.copy2(source / name, extensions / name)
            # Explicitly load only verification; its routing helper is an import.
            (root / "subject.js").write_text("BASELINE\n", encoding="utf-8")
            def git(*args):
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            git("init")
            git("add", "subject.js")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "-c", "commit.gpgsign=false",
                "-c", "core.hooksPath=none", "commit", "-m", "fixture")
            models = {"providers": {"fixture": {"baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                "apiKey": "local-fixture", "api": "openai-completions", "models": [
                    {"id": "fixture", "name": "fixture", "reasoning": False, "input": ["text"],
                     "contextWindow": 131072, "maxTokens": 1024}]}}}
            (agent / "models.yml").write_text(yaml.safe_dump(models), encoding="utf-8")
            (agent / "config.yml").write_text(yaml.safe_dump({"modelRoles": {"default": "fixture/fixture"},
                "retry": {"enabled": False, "modelFallback": False}}), encoding="utf-8")
            cli = Path.home() / "AppData/Roaming/npm/node_modules/gitnexus/dist/cli/index.js"
            (agent / "mcp.json").write_text(json.dumps({"mcpServers": {"gitnexus": {
                "command": shutil.which("node"), "args": [str(cli), "mcp"]}}}), encoding="utf-8")
            marker = base / "verification-runs.txt"
            check = {"id": "fixture", "paths": ["subject.js"], "command": shutil.which("node"),
                "args": ["-e", "const fs=require('fs');fs.appendFileSync(" + json.dumps(str(marker)) + ",'run\\n');const ok=fs.readFileSync('subject.js','utf8').startsWith('GOOD');console.log('# pass '+(ok?1:0)+'\\n# fail '+(ok?0:1));process.exit(ok?0:1)"],
                "evidence": "node-test", "timeoutMs": 3000}
            (agent / "task-verification-policy.json").write_text(json.dumps({"schema": 1,
                "projects": [{"root": str(root), "checks": [check]}]}), encoding="utf-8")
            env = dict(os.environ, PI_CODING_AGENT_DIR=str(agent), NO_PROXY="127.0.0.1,localhost", OMP_MCP_STARTUP_TIMEOUT_MS="10000")
            for key in ("HTTP_PROXY", "HTTPS_PROXY"):
                env.pop(key, None)
            started = time.monotonic()
            child = subprocess.run([shutil.which("omp"), "-p", "Run the local verification fixture.",
                "--model", "fixture/fixture", "--no-extensions", "--extension", str(extensions / "omp-task-verification.js"),
                "--no-skills", "--no-rules", "--no-session", "--no-title", "--no-lsp", "--max-time", "60s"], cwd=root, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=80)
            messages = observed[-1].get("messages", []) if observed else []
            by_id = {m.get("tool_call_id"): str(m.get("content", "")) for m in messages if m.get("role") == "tool"}
            assertions = {
                "completion": child.returncode == 0 and "FIXTURE_COMPLETE" in child.stdout and not errors,
                "passed": '"status":"passed"' in by_id.get("call_1", ""),
                "stale": "files-changed-since-verification" in by_id.get("call_3", ""),
                "failed": '"status":"failed"' in by_id.get("call_4", ""),
                "uncovered": '"status":"unverified"' in by_id.get("call_7", "") and "unknown.js" in by_id.get("call_7", ""),
                "gitnexus_list": "cc-switch" in by_id.get("call_8", ""),
                "gitnexus_context": "acquireCanaryLease" in by_id.get("call_9", "") and "saveCheckpoint" in by_id.get("call_9", ""),
                "gitnexus_impact": "saveCheckpoint" in by_id.get("call_10", "") and "runEscalation" in by_id.get("call_10", ""),
                "automatic_verification": marker.exists() and len(marker.read_text().splitlines()) == 4,
            }
            ok = all(assertions.values())
            report = {"ok": ok, "checks": assertions, "seconds": round(time.monotonic() - started, 2), "requests": len(observed)}
            if not ok:
                # Dummy-key environment only; never use this diagnostic on live credentials.
                report.update(errors=errors, diagnostic=(child.stdout + child.stderr)[-4000:])
                report["run_count"] = len(marker.read_text().splitlines()) if marker.exists() else 0
            print(json.dumps(report), flush=True)
            return 0 if ok else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
