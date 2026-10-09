#!/usr/bin/env python3
"""Jev ops triage — weekly NewAPI/Guardian failure digest with independent judgment.

Signal sources (NewAPI stopped writing error rows to the logs table —
type=5 error logs discontinued 2026-08-01 per an upstream defect, recorded at
scripts/ops/guardian.py:1721; remaining rows are type=2 consume / type=3
admin / a handful of type=7. Error evidence therefore comes from Guardian,
which tails ~/.new-api-local/logs/oneapi-*.log):
  1. channels with status != 1 (manually/auto disabled),
  2. Guardian state.json disabled_channels (reason + time),
  3. Guardian state.json degraded_channels (weight reduction).

Each signal is judged by the local Jev pool (ch181 via gateway) for
transient/persistent classification and severity. REPORT-ONLY: never mutates
channel state, never gates actions.

Bridge contract (scripts/ops/jev_systemone_bridge.mjs):
- OpenAI chat format; last user message content = JSON envelope
  {"type": "jev.systemone", "state", "questions"} — the type field selects
  envelope mode; without it the bridge falls back to its fixed template
  (is_blocking/urgency/route) and answer keys won't match.
- Assistant content = JSON answers keyed by question id.
- Fail-open: if Jev is unreachable, the raw signal table is still printed.

Usage:
  python3 scripts/ops/jev_ops_triage.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

GATEWAY = "http://127.0.0.1:3002"
JEV_MODEL = "jev-latest"
GUARDIAN_STATE = Path.home() / ".omp" / "guardian" / "state.json"
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"
SEV_LABELS = ("low", "medium", "high")

QUESTIONS = {
    "persistence": {
        "type": "choice",
        "instructions": (
            "Classify this channel's failure mode. Judge only from the "
            "evidence in state; do not assume recovery timelines."
        ),
        "criteria": {
            "transient": "temporary condition likely self-recovering "
            "(rate-limit window, brief upstream 5xx); monitoring suffices",
            "persistent": "structural failure needing manual action "
            "(quota/balance exhausted, auth revoked, upstream gone, "
            "repeated hard failures across days)",
            "unknown": "insufficient evidence to classify",
        },
    },
    "severity": {
        "type": "score",
        "instructions": "Rate user-facing impact of this channel's condition.",
        "criteria": [
            "low: cosmetic or single blip, no user impact",
            "medium: degraded capacity or intermittent user-visible errors",
            "high: channel dead or persistently returning user-visible errors",
        ],
    },
}


def gateway_key() -> str:
    text = MODELS_YML.read_text(encoding="utf-8")
    m = re.search(r"zg-newapi.*?apiKey:\s*(\S+)", text, re.S)
    if not m:
        raise RuntimeError("zg-newapi apiKey not found in models.yml")
    return m.group(1)


def collect_signals() -> list[dict]:
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        signals: dict[int, dict] = {}
        for cid, name, status, models in con.execute(
            "SELECT id, name, status, models FROM channels WHERE status != 1"
        ).fetchall():
            signals[cid] = {
                "channel_id": cid,
                "name": name,
                "newapi_status": status,
                "models": (models or "")[:120],
            }
    finally:
        con.close()

    if GUARDIAN_STATE.exists():
        try:
            g = json.loads(GUARDIAN_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            g = {}
        for entry in g.get("disabled_channels") or []:
            cid = entry.get("id")
            row = signals.get(cid)
            if row is None:
                row = {
                    "channel_id": cid,
                    "name": f"ch{cid}",
                    "newapi_status": "?",
                    "models": "",
                }
                signals[cid] = row
            row["guardian"] = (
                f"disabled since {str(entry.get('time', '?'))[:10]} "
                f"(reason: {str(entry.get('reason', '?'))[:90]})"
            )
        for cid, entry in (g.get("degraded_channels") or {}).items():
            cid = int(cid)
            row = signals.setdefault(
                cid,
                {
                    "channel_id": cid,
                    "name": f"ch{cid}",
                    "newapi_status": 1,
                    "models": "",
                },
            )
            row["guardian"] = (
                f"degraded w{entry.get('original_weight')}->"
                f"{entry.get('degraded_weight')} "
                f"({str(entry.get('reason', '?'))[:70]})"
            )
    return list(signals.values())


def ask_jev(state: dict, key: str, timeout: float = 45) -> dict:
    envelope = json.dumps(
        {"type": "jev.systemone", "state": state, "questions": QUESTIONS},
        ensure_ascii=False,
    )
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=json.dumps(
            {
                "model": JEV_MODEL,
                "messages": [{"role": "user", "content": envelope}],
                "max_tokens": 4096,
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8", "replace"))
    return json.loads(out["choices"][0]["message"]["content"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    signals = collect_signals()
    if not signals:
        print("No failure signals (no disabled/degraded channels). Nothing to triage.")
        return 0

    try:
        key = gateway_key()
    except RuntimeError as e:
        print(f"WARN {e}; printing raw table without judgments.")
        key = None

    order = {"persistent": 0, "unknown": 1, "transient": 2}
    for s in signals:
        s["persistence"], s["severity"], s["sev_raw"] = "n/a", "n/a", -1
        if key:
            try:
                answers = ask_jev(s, key)
                p = answers.get("persistence", {})
                s["persistence"] = p.get("choice", "n/a")
                s["p"] = p.get("probabilities", {}).get(s["persistence"])
                sev = answers.get("severity", {}).get("score", "n/a")
                if isinstance(sev, (int, float)) and 0 <= sev <= 2:
                    s["sev_raw"] = sev
                    s["severity"] = f"{SEV_LABELS[round(sev)]} ({sev:.2f})"
                else:
                    s["severity"] = sev
            except Exception as e:  # fail-open, report-only
                s["persistence"] = f"jev-error: {str(e)[:60]}"
    signals.sort(
        key=lambda s: (
            order.get(s["persistence"], 3),
            -s["sev_raw"],
            s["channel_id"],
        )
    )

    lines = [
        f"# Jev ops triage — {time.strftime('%Y-%m-%d %H:%M')} local",
        "",
        "Judgments are advisory signals from an independent model; "
        "accuracy unvalidated. This report never mutates channel state.",
        "",
        "| ch | name | persistence | sev | status | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for s in signals:
        prob = f" (p={s['p']:.2f})" if isinstance(s.get("p"), float) else ""
        lines.append(
            f"| {s['channel_id']} | {s['name']} | {s['persistence']}{prob} "
            f"| {s['severity']} | {s['newapi_status']} "
            f"| {s.get('guardian', 'manual/unknown')} |"
        )
    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
