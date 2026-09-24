"""Native OMP extension contract test using dummy credentials and a local SSE fixture."""
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
    observed = []
    phase = "save"
    checkpoint = {"goal": "FIXTURE_GOAL", "nextStep": "Inspect different evidence",
                  "completionCriteria": "Fixture checks pass", "constraints": ["Read only"],
                  "evidence": ["File missing twice"], "rejectedHypotheses": ["File exists"],
                  "verifiedTests": ["Missing-file read failed"]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(request)
            step = len(observed)
            if phase == "child-error":
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"fixture unavailable","type":"server_error"}}')
                return
            if phase == "save" and step <= 3:
                name = "read" if step <= 2 else "problem_checkpoint"
                args = {"path": "fixture-missing.txt"} if step <= 2 else checkpoint
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"call_{step}",
                         "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            elif phase in ("guard", "child") and step <= (10 if phase == "child" else 9):
                name = "write" if step == 1 else "read"
                args = {"path": "forbidden.txt", "content": "must not write"} if step == 1 else {
                    "path": ".." if step == 2 else "evidence.txt", "limit": 10}
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"guard_{step}",
                         "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "FIXTURE_COMPLETE"}
                finish = "stop"
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
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="omp-problem-solving-") as directory:
            root = Path(directory)
            agent = root / "agent"
            extensions = agent / "extensions"
            extensions.mkdir(parents=True)
            source = Path(__file__).resolve().parent
            # Mirror installed siblings. Only the reviewer explicitly loads
            # its guard, which must never be active in a normal session.
            shutil.copy2(source / "omp-problem-solving.js", extensions / "omp-problem-solving.js")
            for name in ("omp-sota-escalation.js", "omp-model-routing-observability.js"):
                shutil.copy2(source / name, extensions / name)
            (extensions / "review").mkdir()
            shutil.copy2(source / "review/omp-review-guard.js", extensions / "review/omp-review-guard.js")
            models = {"providers": {"fixture": {"baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                       "apiKey": "local-fixture", "api": "openai-completions", "models": [
                           {"id": name, "name": name, "reasoning": False, "input": ["text"],
                            "contextWindow": 32768, "maxTokens": 1024} for name in ("fixture", "fallback")]}}}
            (agent / "models.yml").write_text(yaml.safe_dump(models), encoding="utf-8")
            (agent / "config.yml").write_text(yaml.safe_dump({"modelRoles": {"default": "fixture/fixture"},
                "retry": {"maxRetries": 3, "baseDelayMs": 10, "maxDelayMs": 50, "modelFallback": True,
                          "fallbackChains": {"fixture/fixture": ["fixture/fallback"]}}}), encoding="utf-8")
            env = dict(os.environ, PI_CODING_AGENT_DIR=str(agent), NO_PROXY="127.0.0.1,localhost")
            for key in ("HTTP_PROXY", "HTTPS_PROXY"):
                env.pop(key, None)
            (root / "evidence.txt").write_text("BOUNDED_EVIDENCE", encoding="utf-8")
            for phase in ("save", "restore", "guard", "child", "child-error"):
                observed.clear()
                started = time.monotonic()
                extra = ["--no-extensions", "--extension", str(source / "review/omp-review-guard.js")] if phase == "guard" else []
                selected_tools = "read,write" if phase == "guard" else "read,problem_checkpoint"
                command = [shutil.which("omp"), "-p", "Run fixture", "--model", "fixture/fixture",
                    "--no-skills", "--no-session", "--no-title", "--max-time", "20s", "--tools", selected_tools, *extra]
                if phase.startswith("child"):
                    runner = root / "review-runner.mjs"
                    runner.write_text("import {runSotaChild,safeExecArgs} from " + json.dumps((source / "omp-sota-escalation.js").as_uri()) + ";\n"
                        "const result = await runSotaChild(safeExecArgs('fixture/fixture',{reason:'explicit',userPrompt:'Review evidence.txt'},['evidence.txt']),{cwd:process.cwd(),timeoutMs:20000});\n"
                        "process.stdout.write(result.stdout); console.log(JSON.stringify({reviewKilled:result.killed})); process.stderr.write(result.stderr); process.exitCode=result.code===0?0:1;\n", encoding="utf-8")
                    command = [shutil.which("node"), str(runner)]
                child = subprocess.run(command,
                    cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=35)
                messages = json.dumps([r.get("messages") for r in observed])
                tool_names = [t["function"]["name"] for r in observed[:1] for t in r.get("tools", [])]
                memory_files = list((agent / "problem-solving-memory").glob("*.json"))
                ok = child.returncode == 0 and "FIXTURE_COMPLETE" in child.stdout and bool(memory_files)
                if phase == "save":
                    ok = ok and "problem_checkpoint" in tool_names and "same tool action has failed twice" in messages
                    ok = ok and "Project checkpoint saved" in messages and len(observed) == 4
                elif phase == "restore":
                    ok = ok and "Historical project checkpoint" in messages and "FIXTURE_GOAL" in messages
                elif phase == "child-error":
                    # The provider transport has its own retries despite the
                    # disabled session retry layer. Assert the actual contract:
                    # same selected model, one child, and the hard deadline.
                    ok = child.returncode != 0 and bool(observed) and all(r.get("model") == "fixture" for r in observed)
                    ok = ok and time.monotonic() - started < 26 and '"reviewKilled":true' in child.stdout
                else:
                    ok = ok and not (root / "forbidden.txt").exists()
                    if phase == "guard":
                        ok = ok and "Reviewer is read-only" in messages
                    ok = ok and "within its workspace" in messages and "tool budget exhausted" in messages
                    ok = ok and "BOUNDED_EVIDENCE" in messages
                report = {"scenario": phase, "ok": ok, "seconds": round(time.monotonic() - started, 2),
                          "requests": len(observed), "checkpoint_files": len(memory_files), "tools": tool_names,
                          "exit_code": child.returncode}
                if not ok:
                    # Isolated environment contains dummy credentials only.
                    report["fixture_diagnostic"] = (child.stderr + child.stdout)[-3000:]
                    report["guard_checks"] = {word: word in messages for word in (
                        "Reviewer is read-only", "within its workspace", "tool budget exhausted", "BOUNDED_EVIDENCE")}
                print(json.dumps(report), flush=True)
                results.append(ok)
    finally:
        server.shutdown()
        server.server_close()
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
