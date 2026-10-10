#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure one model across named pools, through each channel's own machinery.

Two uses:
- decide the deepseek-v4-flash head before running adjust_deepseek_v4_flash_pool.py
- check whether an opus pool leg actually still serves (--model claude-opus-5)

docs/ops/omp-driftfix-dsv4f-head-2026-10-10.md demoted ch118 seeseed to p25 because
its own relay returned deterministic 500 "do request failed". ch118 keeps
auto_ban=0, so promoting it to head without re-measuring would put a permanent
failing hop in front of every request.

Uses GET/POST /api/channel/test/{id} (the channel's own key and machinery, routing
untouched). Read-only: nothing here changes priority, status or abilities.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import time
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_HEADS = {118: "seeseed (candidate main)", 180: "agentrouter (current main)"}


def _redact(text: str) -> str:
    return re.sub(r"\b(?:sk-|gsk-|pk-|tok-)[A-Za-z0-9_-]{6,}\b", "[REDACTED]", text)


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke module from {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe(smoke, headers, cid: int, attempts: int, gap: float, model: str) -> list[dict]:
    url = f"{smoke.NEWAPI_BASE}/api/channel/test/{cid}?model={model}"
    method = "GET"
    results = []
    for i in range(1, attempts + 1):
        t0 = time.monotonic()
        try:
            status, body = smoke.http_json(url, method=method, headers=headers, timeout=120)
        except Exception as exc:
            results.append({"ok": False, "status": None, "ms": int((time.monotonic() - t0) * 1000),
                            "err": _redact(f"{type(exc).__name__}: {exc}")[:160]})
            print(f"  ch{cid} [{i}/{attempts}] EXC {results[-1]['err']}", flush=True)
            time.sleep(gap)
            continue
        # this fork's precedent calls the test endpoint without a method; fall back
        # to POST if the router rejects GET.
        if i == 1 and status in (404, 405) and method == "GET":
            method = "POST"
            status, body = smoke.http_json(url, method=method, headers=headers, timeout=120)
            print(f"  ch{cid}: GET rejected, using POST", flush=True)
        ok = bool(body.get("success"))
        err = _redact(str(body.get("message") or body.get("error") or ""))[:160]
        results.append({"ok": ok, "status": status, "ms": int((time.monotonic() - t0) * 1000), "err": err})
        print(f"  ch{cid} [{i}/{attempts}] HTTP {status} success={ok} "
              f"api_time={body.get('time')}ms wall={results[-1]['ms']}ms"
              f"{(' err=' + err) if err else ''}", flush=True)
        if i < attempts:
            time.sleep(gap)
    return results


def parse_channels(specs: list[str] | None) -> dict[int, str]:
    if not specs:
        return dict(DEFAULT_HEADS)
    heads: dict[int, str] = {}
    for spec in specs:
        cid_text, _, label = spec.partition("=")
        heads[int(cid_text)] = label or f"ch{cid_text}"
    return heads


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=6, help="probes per channel (default 6)")
    parser.add_argument("--gap", type=float, default=5.0, help="seconds between probes")
    parser.add_argument("--min-ok", type=int, default=6, help="successes required to endorse the candidate")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model to test (default {DEFAULT_MODEL})")
    parser.add_argument("--channels", nargs="*", metavar="ID[=LABEL]",
                        help="channels to probe (default the deepseek-v4-flash head pair 118/180)")
    args = parser.parse_args()

    heads = parse_channels(args.channels)
    candidate = next(iter(heads)) if args.channels else 118

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    print(f"model={args.model} attempts={args.attempts} gap={args.gap}s")
    all_results = {}
    for cid, label in heads.items():
        print(f"ch{cid} {label}:")
        all_results[cid] = probe(smoke, headers, cid, args.attempts, args.gap, args.model)

    print("\n--- summary ---")
    for cid, label in heads.items():
        res = all_results[cid]
        ok_n = sum(1 for r in res if r["ok"])
        lat = sorted(r["ms"] for r in res if r["ok"])
        med = lat[len(lat) // 2] if lat else None
        errs = {r["err"] for r in res if not r["ok"] and r["err"]}
        print(f"ch{cid} {label}: {ok_n}/{len(res)} ok, median_wall={med}ms"
              f"{('; ' + ' | '.join(sorted(errs))[:200]) if errs else ''}")

    ok_n = sum(1 for r in all_results[candidate] if r["ok"])
    endorse = ok_n >= args.min_ok
    print(f"\nverdict: ch{candidate} {'ENDORSED' if endorse else 'NOT endorsed'} "
          f"({ok_n}/{len(all_results[candidate])} ok, need {args.min_ok})")
    return 0 if endorse else 2


if __name__ == "__main__":
    sys.exit(main())
