#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turnstile solver client for https://solver.000.moe (v3.5.2).

Endpoints (all POST + JSON):
  /getBalance        clientKey in body       free
  /createTask        clientKey in body       YesCaptcha / CapSolver style task
  /getTaskResult     clientKey in body       poll; free
  /solve             X-API-Key / Bearer      sync: return token for url+sitekey

Billing: 1 credit per successful solve; failure/timeout auto-refunded.
Busy: ERROR_NO_SLOT_AVAILABLE / HTTP 429 -> retry later.

Key never printed; CLI reads it from --client-key, SOLVER_CLIENT_KEY env, or a file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_ENV = "SOLVER_BASE_URL"
DEFAULT_BASE = "https://solver.000.moe"
KEY_ENV = "SOLVER_CLIENT_KEY"
POLL_INTERVAL = 3.0
DEFAULT_TASK_TIMEOUT = 120.0


def _base() -> str:
    return os.environ.get(BASE_ENV, DEFAULT_BASE)


class SolverError(RuntimeError):
    """errorId != 0 or HTTP error from the solver gateway."""


def _redact(text: str, key: str) -> str:
    return text.replace(key, "<redacted>") if key else text


def http_json(url: str, key: str, body: dict | None = None, header_auth: bool = False,
              timeout: int = 60, method: str = "POST") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }
    if header_auth:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"errorId": exc.code, "errorDescription": raw[:300]}
        raise SolverError(_redact(json.dumps(payload, ensure_ascii=False), key))


def _require_key(flag: str | None) -> str:
    if flag:
        return flag
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV]
    path = Path(os.environ.get("SOLVER_KEY_FILE", ""))
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    raise SystemExit("missing client key: use --client-key, %s env, or SOLVER_KEY_FILE" % KEY_ENV)


def get_balance(key: str) -> dict:
    return http_json(f"{_base()}/getBalance", key, {"clientKey": key})


def create_task(key: str, url: str, sitekey: str, action: str | None = None,
                cdata: str | None = None) -> dict:
    task: dict = {"type": "TurnstileTaskProxyless", "websiteURL": url, "websiteKey": sitekey}
    metadata = {}
    if action:
        metadata["action"] = action
    if cdata:
        metadata["cdata"] = cdata
    if metadata:
        task["metadata"] = metadata
    return http_json(f"{_base()}/createTask", key, {"clientKey": key, "task": task})


def get_task_result(key: str, task_id: str) -> dict:
    return http_json(f"{_base()}/getTaskResult", key, {"clientKey": key, "taskId": task_id})


def solve_token(key: str, url: str, sitekey: str, action: str | None = None,
                cdata: str | None = None, timeout: float | None = None) -> dict:
    body: dict = {"url": url, "sitekey": sitekey}
    if action:
        body["action"] = action
    if cdata:
        body["cdata"] = cdata
    if timeout is not None:
        body["timeout"] = max(timeout, 5.0)
    return http_json(f"{_base()}/solve", key, body, header_auth=True, timeout=180)


def solve_via_task(key: str, url: str, sitekey: str, action: str | None = None,
                   cdata: str | None = None, timeout: float = DEFAULT_TASK_TIMEOUT) -> dict:
    created = create_task(key, url, sitekey, action, cdata)
    if created.get("errorId"):
        raise SolverError(_redact(json.dumps(created, ensure_ascii=False), key))
    task_id = created.get("taskId") or created.get("task_id")
    if not task_id:
        raise SolverError(_redact(json.dumps(created, ensure_ascii=False), key))
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        result = get_task_result(key, str(task_id))
        if result.get("errorId"):
            raise SolverError(_redact(json.dumps(result, ensure_ascii=False), key))
        if result.get("status") == "ready":
            solution = result.get("solution") or {}
            token = solution.get("token") or solution.get("value")
            return {"token": token, "status": "ready", "taskId": task_id}
        time.sleep(POLL_INTERVAL)
    raise SolverError(f"task {task_id} still processing after {timeout:.0f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-key", help="solver client key (default: env/file)")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"gateway base URL (default: {DEFAULT_BASE})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_balance = sub.add_parser("balance", help="query balance (free)")
    p_balance.set_defaults(fn="balance")

    p_health = sub.add_parser("health", help="gateway health (no auth)")
    p_health.set_defaults(fn="health")

    p_solve = sub.add_parser("solve", help="sync /solve: url + sitekey -> token (1 credit)")
    p_solve.set_defaults(fn="solve")
    p_solve.add_argument("url")
    p_solve.add_argument("sitekey")
    p_solve.add_argument("--action")
    p_solve.add_argument("--cdata")
    p_solve.add_argument("--timeout", type=float)

    p_task = sub.add_parser("task", help="createTask + poll getTaskResult (1 credit)")
    p_task.set_defaults(fn="task")
    p_task.add_argument("url")
    p_task.add_argument("sitekey")
    p_task.add_argument("--action")
    p_task.add_argument("--cdata")
    p_task.add_argument("--timeout", type=float, default=DEFAULT_TASK_TIMEOUT)

    args = parser.parse_args()
    if args.base != DEFAULT_BASE:
        os.environ[BASE_ENV] = args.base

    if args.fn == "health":
        out = http_json(f"{_base()}/health", "", method="GET")
        print(json.dumps({k: out.get(k) for k in
                          ("status", "backend", "solver", "active", "capacity", "queued")},
                         ensure_ascii=False))
        return 0

    key = _require_key(args.client_key)
    if args.fn == "balance":
        out = get_balance(key)
        if out.get("errorId"):
            print(_redact(json.dumps(out, ensure_ascii=False), key))
            return 1
        print(f"balance: {out.get('balance')}")
        return 0

    assert args.fn in ("solve", "task")
    if args.fn == "solve":
        out = solve_token(key, args.url, args.sitekey, args.action, args.cdata, args.timeout)
        print(_redact(json.dumps(out, ensure_ascii=False), key))
        return 0 if out.get("token") else 1

    out = solve_via_task(key, args.url, args.sitekey, args.action, args.cdata, args.timeout)
    print(_redact(json.dumps(out, ensure_ascii=False), key))
    return 0 if out.get("token") else 1


if __name__ == "__main__":
    sys.exit(main())