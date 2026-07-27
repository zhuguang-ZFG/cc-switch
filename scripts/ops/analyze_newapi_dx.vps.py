#!/usr/bin/env python3
"""NewAPI DX ops loop: detect → remediate (in-band) → report.

Cursor/ops owner entry on the VPS.
  python3 /opt/new-api/analyze_newapi_dx.py           # apply in-band changes
  python3 /opt/new-api/analyze_newapi_dx.py --dry-run # report only
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB = Path("/opt/new-api/data/one-api.db")
REPORT_DIR = Path("/opt/new-api/reports")
BACKUP_DIR = Path("/opt/new-api/backups")
ENV_FILE = Path("/opt/new-api/kiro-guard.env")
STATE_FILE = Path("/opt/new-api/dx_ops_state.json")
LOCK_FILE = Path("/opt/new-api/dx_ops.lock")
STATUS_MD = Path("/opt/new-api/STATUS.md")
GUARD_UNITS = [
    "kiro-guard",
    "kiro-guard-100xlabs",
    "kiro-guard-100x-8403",
    "kiro-guard-100x-8404",
    "kiro-guard-100x-8405",
]
OPUS_POOL = [9, 10, 20, 60]
AR_POOL = [118, 119, 120]  # secondary; auto-demote when slow
WEIGHT_DENYLIST = {11, 81}  # never auto-weight / never revive
# intended priority tiers (weight bands applied within tier)
LAST_RESORT = set()  # #81 pinned off; do not auto-revive
LOOKBACK_H = 24
MIN_SAMPLES = 10
MIN_SAMPLES_HARD = 5  # enough to force-demote a stalling channel
WEIGHT_MIN, WEIGHT_MAX = 1, 50
DELTA_CAP = 15
WEIGHT_COOLDOWN_S = 6 * 3600
# latency caps (collect_opus_latency stores ms)
P50_HARD_MS = 35_000  # cap weight <= 8
P90_HARD_MS = 90_000  # cap weight <= 3
AR_P50_HARD_MS = 45_000  # AR backup: cap weight <= 3
AR_P50_PARK_MS = 80_000  # AR: weight=1 (keep channel, starve traffic)
SOFT_MIN_EVENTS = 20
SHORT_OUT_BOUNDS = (16, 64)
GATEWAY = "http://127.0.0.1:3000"
MANAGED_ENV_KEYS = (
    "KIRO_GUARD_SHORT_OUT",
    "KIRO_GUARD_SHORT_IN",
    "KIRO_GUARD_SHORT_MAX_TOKENS",
    "KIRO_GUARD_SOFT_RETRY",
)


class ShError(RuntimeError):
    """External command failed (timeout or non-zero rc). Carries cmd context."""


def sh(cmd: List[str], timeout: int = 60, check: bool = True) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ShError(f"timeout({timeout}s): {' '.join(str(c) for c in cmd)}") from e
    if check and r.returncode != 0:
        tail = ((r.stdout or "") + (r.stderr or "")).strip()[-300:]
        raise ShError(f"rc={r.returncode}: {' '.join(str(c) for c in cmd)} :: {tail}")
    return (r.stdout or "") + (r.stderr or "")


def pct(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(len(s) * p) - 1)]


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_soft_journal(hours: int) -> Dict[str, Any]:
    units = []
    for u in GUARD_UNITS:
        units += ["-u", f"{u}.service"]
    try:
        out = sh(
            ["journalctl", *units, "--since", f"{hours} hours ago", "--no-pager", "-o", "cat"],
            timeout=90,
        )
    except ShError as e:
        # 证据采集失败：不得静默降级为"无证据"，由 main 写入 escalate
        return {
            "soft_retry": 0,
            "soft_exhausted": 0,
            "hard_fail": 0,
            "reasons": {},
            "lines_scanned": 0,
            "error": f"证据采集失败 (journalctl): {e}",
        }
    soft_retry = 0
    soft_exhausted = 0
    hard = 0
    reasons: Dict[str, int] = {}
    for line in out.splitlines():
        if "soft_retry" in line:
            soft_retry += 1
            m = re.search(r"reason=([a-zA-Z0-9_:`.-]+)", line)
            if m:
                reasons[m.group(1)] = reasons.get(m.group(1), 0) + 1
        if "soft_exhausted" in line:
            soft_exhausted += 1
            m = re.search(r"reason=([a-zA-Z0-9_:`.-]+)", line)
            if m:
                key = "exhausted:" + m.group(1)
                reasons[key] = reasons.get(key, 0) + 1
        if "hard_fail" in line:
            hard += 1
    return {
        "soft_retry": soft_retry,
        "soft_exhausted": soft_exhausted,
        "hard_fail": hard,
        "reasons": reasons,
        "lines_scanned": len(out.splitlines()),
    }


def collect_opus_latency(hours: int) -> Dict[int, dict]:
    watch = list(dict.fromkeys(OPUS_POOL + AR_POOL))
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    now = int(time.time())
    since = now - hours * 3600
    # use_time is seconds in new-api logs typically
    rows = con.execute(
        """
        SELECT channel_id, use_time, prompt_tokens, completion_tokens, model_name
        FROM logs
        WHERE created_at>? AND type=2 AND model_name LIKE '%opus%'
          AND channel_id IN ({})
        """.format(
            ",".join("?" * len(watch))
        ),
        (since, *watch),
    ).fetchall()
    con.close()
    by: Dict[int, list] = {cid: [] for cid in watch}
    shorts = 0
    for cid, use_time, pt, ct, _model in rows:
        if cid not in by:
            continue
        # prefer use_time seconds → ms
        try:
            ut = float(use_time or 0)
        except Exception:
            ut = 0.0
        ms = ut * 1000.0  # use_time is fixed seconds in current schema
        by[cid].append(
            {
                "ms": ms,
                "pt": int(pt or 0),
                "ct": int(ct or 0),
            }
        )
        if int(pt or 0) >= 2000 and 0 < int(ct or 0) <= 32:
            shorts += 1
    out = {}
    for cid, items in by.items():
        xs = [i["ms"] for i in items if i["ms"] > 0]
        out[cid] = {
            "n": len(items),
            "p50": pct(xs, 0.5),
            "p90": pct(xs, 0.9),
            "short_completion_n": sum(
                1 for i in items if i["pt"] >= 2000 and 0 < i["ct"] <= 32
            ),
        }
    out["_meta"] = {"rows": len(rows), "short_completion_total": shorts}
    return out


def current_weights() -> Dict[int, dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    for cid in list(dict.fromkeys(OPUS_POOL + AR_POOL)):
        if cid in WEIGHT_DENYLIST:
            continue
        ch = con.execute(
            "SELECT name, status, priority, IFNULL(weight,0) FROM channels WHERE id=?",
            (cid,),
        ).fetchone()
        if not ch:
            continue
        out[cid] = {
            "name": ch[0],
            "status": ch[1],
            "priority": ch[2],
            "weight": int(ch[3] or 0),
        }
    con.close()
    return out


def _clamp_delta(old: int, target: int, escalate: List[str], cid: int) -> int:
    delta = target - old
    if abs(delta) > DELTA_CAP:
        target = old + (DELTA_CAP if delta > 0 else -DELTA_CAP)
        escalate.append(f"#{cid} delta clamped to ±{DELTA_CAP}")
    return max(WEIGHT_MIN, min(WEIGHT_MAX, target))


def _latency_cap(cid: int, target: int, p50: float, p90: float, n: int, escalate: List[str]) -> int:
    """Hard latency caps so a stalling upstream cannot keep high weight."""
    if n < MIN_SAMPLES_HARD:
        return target
    capped = target
    if p90 >= P90_HARD_MS:
        capped = min(capped, 3)
        escalate.append(f"#{cid} p90={p90/1000:.0f}s>=90s → weight<={capped}")
    elif p50 >= P50_HARD_MS:
        capped = min(capped, 8)
        escalate.append(f"#{cid} p50={p50/1000:.0f}s>=35s → weight<={capped}")
    if cid in AR_POOL:
        if p50 >= AR_P50_PARK_MS:
            capped = min(capped, 1)
            escalate.append(f"#{cid} AR p50={p50/1000:.0f}s>=80s → park w1")
        elif p50 >= AR_P50_HARD_MS:
            capped = min(capped, 3)
            escalate.append(f"#{cid} AR p50={p50/1000:.0f}s>=45s → weight<=3")
    return capped


def needs_force_demote(latency: Dict[int, dict], cur: Dict[int, dict], suggested: Dict[int, int]) -> bool:
    """Bypass cooldown when we must cut a slow channel's weight."""
    for cid, w in suggested.items():
        old = (cur.get(cid) or {}).get("weight")
        if old is None:
            continue
        if w >= old:
            continue
        lat = latency.get(cid) or {}
        n = lat.get("n") or 0
        p50 = lat.get("p50") or 0
        p90 = lat.get("p90") or 0
        if n >= MIN_SAMPLES_HARD and (p50 >= P50_HARD_MS or p90 >= P90_HARD_MS or (cid in AR_POOL and p50 >= AR_P50_HARD_MS)):
            return True
    return False


def suggest_weights(latency: Dict[int, dict], cur: Dict[int, dict]) -> Tuple[Dict[int, int], List[str]]:
    """Return suggested weights and escalate reasons."""
    escalate: List[str] = []
    # rank enabled primary pool with enough samples by p50 then p90
    ranked = []
    for cid in OPUS_POOL:
        if cid not in cur or cur[cid]["status"] != 1:
            continue
        lat = latency.get(cid) or {}
        n = lat.get("n") or 0
        if n < MIN_SAMPLES:
            escalate.append(f"#{cid} samples={n}<{MIN_SAMPLES} keep weight={cur[cid]['weight']}")
            continue
        ranked.append((cid, lat.get("p50") or 1e9, lat.get("p90") or 1e9, n))
    ranked.sort(key=lambda x: (x[1], x[2]))

    # best→50; slower ranks get lower bands. Never raise a channel that is
    # clearly slower than the current best (anti-stall: avoid re-promoting #20).
    band_for_rank = [50, 40, 28, 16, 8, 4]
    suggested: Dict[int, int] = {}
    ri = 0
    best_p50 = float(ranked[0][1]) if ranked else 0.0
    for cid, p50, p90, n in ranked:
        p50f = float(p50)
        p90f = float(p90)
        if cid in LAST_RESORT:
            target = 3
        else:
            target = band_for_rank[min(ri, len(band_for_rank) - 1)]
            ri += 1
        old = cur[cid]["weight"]
        if old == 0:
            # manually zeroed (operator parked the channel): never auto-revive
            suggested[cid] = 0
            escalate.append(f"#{cid} manually zeroed, kept")
            continue
        # slower than 1.6× best: never raise
        if best_p50 > 0 and p50f > best_p50 * 1.6 and target > old:
            target = old
            escalate.append(f"#{cid} no-raise (p50 {p50f/1000:.0f}s > 1.6× best {best_p50/1000:.0f}s)")
        # slower than 2× best: push down toward ≤16
        if best_p50 > 0 and p50f > best_p50 * 2.0:
            target = min(target, 16)
            escalate.append(f"#{cid} slow-vs-best cap w<=16 (p50 {p50f/1000:.0f}s)")
        target = _clamp_delta(old, target, escalate, cid)
        target = _latency_cap(cid, target, p50f, p90f, n, escalate)
        if target < old - DELTA_CAP and (p50f >= P50_HARD_MS or p90f >= P90_HARD_MS):
            escalate.append(f"#{cid} force demote beyond Δcap (stall)")
        elif target < old - DELTA_CAP:
            target = old - DELTA_CAP
        # final: never raise slower-than-best channels
        if best_p50 > 0 and p50f > best_p50 * 1.6:
            target = min(target, old)
        suggested[cid] = max(WEIGHT_MIN, min(WEIGHT_MAX, target))

    for cid in OPUS_POOL:
        if cid in cur and cid not in suggested:
            suggested[cid] = cur[cid]["weight"]

    # AR secondary: never auto-raise; demote/park when slow
    for cid in AR_POOL:
        if cid not in cur or cur[cid]["status"] != 1:
            continue
        lat = latency.get(cid) or {}
        n = int(lat.get("n") or 0)
        old = int(cur[cid]["weight"])
        if old == 0:
            suggested[cid] = 0
            escalate.append(f"#{cid} manually zeroed, kept")
            continue
        p50 = float(lat.get("p50") or 0)
        p90 = float(lat.get("p90") or 0)
        ceiling = 8 if cid == 118 else 3
        target = min(old, ceiling)
        if n >= MIN_SAMPLES_HARD:
            if p50 >= AR_P50_PARK_MS or p90 >= P90_HARD_MS:
                target = 1
                escalate.append(f"#{cid} AR park w1 (p50={p50/1000:.0f}s p90={p90/1000:.0f}s n={n})")
            elif p50 >= AR_P50_HARD_MS:
                target = min(target, 3)
                escalate.append(f"#{cid} AR demote w<=3 (p50={p50/1000:.0f}s n={n})")
        if target > old:
            target = old
        suggested[cid] = max(WEIGHT_MIN, min(WEIGHT_MAX, int(target)))
    return suggested, escalate


def apply_weights(suggested: Dict[int, int], dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        return {"applied": False, "dry_run": True, "suggested": suggested}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = BACKUP_DIR / f"one-api.before-dx-weights-{ts}.db"
    con = sqlite3.connect(str(DB))
    bcon = sqlite3.connect(str(bak))
    con.backup(bcon)
    bcon.close()
    changed = []
    for cid, w in suggested.items():
        if cid in WEIGHT_DENYLIST:
            continue
        row = con.execute(
            "SELECT IFNULL(weight,0), priority FROM channels WHERE id=?", (cid,)
        ).fetchone()
        if not row:
            continue
        old, pri = int(row[0] or 0), row[1]
        if old == w:
            continue
        con.execute("UPDATE channels SET weight=? WHERE id=?", (w, cid))
        con.execute(
            "UPDATE abilities SET weight=?, priority=? WHERE channel_id=? AND enabled=1",
            (w, pri, cid),
        )
        changed.append({"id": cid, "from": old, "to": w})
    con.commit()
    con.close()
    # keep only the newest 10 weight backups
    backups = sorted(BACKUP_DIR.glob("one-api.before-dx-weights-*.db"))
    for old_bak in backups[:-10]:
        try:
            old_bak.unlink()
        except OSError:
            pass
    restarted = False
    restart_error = None
    if changed:
        try:
            sh(["podman", "restart", "new-api"], timeout=120)
            time.sleep(6)
            restarted = True
        except ShError as e:
            restart_error = str(e)
    return {
        "applied": True,
        "backup": str(bak),
        "changed": changed,
        "suggested": suggested,
        "restarted": restarted,
        "restart_error": restart_error,
    }


def read_env_file() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    # defaults from guard
    env.setdefault("KIRO_GUARD_SHORT_OUT", "32")
    env.setdefault("KIRO_GUARD_SHORT_IN", "2000")
    env.setdefault("KIRO_GUARD_SHORT_MAX_TOKENS", "256")
    env.setdefault("KIRO_GUARD_SOFT_RETRY", "1")
    return env


def write_env_file(env: Dict[str, str], dry_run: bool) -> Dict[str, Any]:
    """Update managed soft-trunc knobs only; preserve other env keys (CONTENT_BLOCK, etc.)."""
    if dry_run:
        return {"applied": False, "dry_run": True, "env": {k: env.get(k) for k in MANAGED_ENV_KEYS}}
    if ENV_FILE.exists():
        bak = Path(str(ENV_FILE) + f".bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        bak.write_text(ENV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        raw_lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    else:
        bak = None
        raw_lines = []
    out_lines: List[str] = []
    seen = set()
    for line in raw_lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            out_lines.append(line)
            continue
        k, _v = s.split("=", 1)
        k = k.strip()
        if k in MANAGED_ENV_KEYS:
            out_lines.append(f"{k}={env.get(k, _v)}")
            seen.add(k)
        else:
            out_lines.append(line)
    for k in MANAGED_ENV_KEYS:
        if k not in seen:
            out_lines.append(f"{k}={env.get(k, '')}")
    if not any(l.startswith("# managed by analyze_newapi_dx") for l in out_lines):
        out_lines.insert(0, "# managed by analyze_newapi_dx.py — soft-truncation knobs (other keys preserved)")
    ENV_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    # ensure units load EnvironmentFile
    ensure_environment_file()
    sh(["systemctl", "daemon-reload"], timeout=30)
    # serial rolling restart: one unit at a time, verify active, stop on first failure
    unit_restart_failed: List[Dict[str, str]] = []
    for unit in GUARD_UNITS:
        try:
            sh(["systemctl", "restart", unit], timeout=60)
            active = sh(["systemctl", "is-active", unit], timeout=20).strip()
            if active != "active":
                raise ShError(f"{unit} is-active={active!r} after restart")
        except ShError as e:
            unit_restart_failed.append({"unit": unit, "error": str(e)})
            break
    # selftest
    st = sh(
        ["bash", "-lc", "KIRO_GUARD_SELFTEST=1 /usr/bin/python3 /opt/new-api/kiro_guard.py"],
        timeout=20,
    )
    return {
        "applied": True,
        "bak": str(bak) if bak else None,
        "env": {k: env.get(k) for k in MANAGED_ENV_KEYS},
        "selftest": st.strip(),
        "units": sh(["systemctl", "is-active", *GUARD_UNITS], timeout=20, check=False).strip().splitlines(),
        "unit_restart_failed": unit_restart_failed,
    }


def ensure_environment_file() -> None:
    """Add EnvironmentFile=-/opt/new-api/kiro-guard.env to guard units if missing."""
    for unit in GUARD_UNITS:
        path = Path(f"/etc/systemd/system/{unit}.service")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "kiro-guard.env" in text:
            continue
        # insert after [Service]
        text2 = text.replace(
            "[Service]\n",
            "[Service]\nEnvironmentFile=-/opt/new-api/kiro-guard.env\n",
            1,
        )
        if text2 != text:
            bak = path.with_suffix(path.suffix + f".bak.envfile-{datetime.now().strftime('%Y%m%d%H%M%S')}")
            bak.write_text(text, encoding="utf-8")
            path.write_text(text2, encoding="utf-8")


def decide_soft_tuning(
    soft: dict, latency: dict, env: Dict[str, str]
) -> Tuple[Optional[Dict[str, str]], List[str], str]:
    """Return (new_env or None, escalate, rationale)."""
    escalate: List[str] = []
    events = soft["soft_retry"] + soft["soft_exhausted"]
    short_total = (latency.get("_meta") or {}).get("short_completion_total") or 0
    rationale = []

    try:
        short_out = int(env["KIRO_GUARD_SHORT_OUT"])
        short_in = int(env["KIRO_GUARD_SHORT_IN"])
    except Exception:
        return None, ["invalid env ints"], "skip"

    # Prefer journal; fall back to consume-log short rate
    if events < SOFT_MIN_EVENTS and short_total < SOFT_MIN_EVENTS:
        escalate.append(
            f"soft evidence sparse (journal_events={events}, log_shorts={short_total}) — no threshold change"
        )
        return None, escalate, "insufficient evidence"

    new_env = dict(env)
    # If many soft_exhausted or many short completions, slightly loosen short gate (raise OUT)
    # so borderline short answers are less likely false-positive... Actually:
    # - high short_completion in logs that still returned 200 means soft gate MISSED → tighten (lower OUT or lower IN)
    # - high soft_exhausted with later success on other channels is working as designed
    # - high soft_retry that recovers often → gate OK
    if short_total >= SOFT_MIN_EVENTS and events < SOFT_MIN_EVENTS:
        # misses: tighten short detection (raise SHORT_OUT threshold? wait:
        # short_completion gate flags output_tokens <= SHORT_OUT
        # more misses of fake-complete → increase SHORT_OUT to catch longer fakes
        new_out = min(SHORT_OUT_BOUNDS[1], short_out + 8)
        if new_out != short_out:
            new_env["KIRO_GUARD_SHORT_OUT"] = str(new_out)
            rationale.append(
                f"log short_completions={short_total} with few soft_* → raise SHORT_OUT {short_out}->{new_out} to catch more soft truncations"
            )
    elif soft["soft_exhausted"] >= max(10, soft["soft_retry"] // 2):
        # many exhausted → maybe too aggressive; loosen by lowering SHORT_OUT
        new_out = max(SHORT_OUT_BOUNDS[0], short_out - 8)
        if new_out != short_out:
            new_env["KIRO_GUARD_SHORT_OUT"] = str(new_out)
            rationale.append(
                f"soft_exhausted={soft['soft_exhausted']} high → lower SHORT_OUT {short_out}->{new_out} to reduce false positives"
            )
    else:
        rationale.append("soft metrics within band — keep thresholds")
        return None, escalate, "; ".join(rationale)

    if new_env == env:
        return None, escalate, "; ".join(rationale) or "no change"
    return new_env, escalate, "; ".join(rationale)


def smoke() -> List[dict]:
    # use local admin token from options? better: read first user token from DB without printing
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # tokens table key
    tok = None
    tok_id = None
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(tokens)").fetchall()}
        # prefer an enabled token with ample quota (unlimited first, then largest remain_quota)
        if "unlimited_quota" in cols and "remain_quota" in cols:
            order = "unlimited_quota DESC, remain_quota DESC"
        elif "remain_quota" in cols:
            order = "remain_quota DESC"
        else:
            order = "id"
        row = con.execute(
            f"SELECT id, key FROM tokens WHERE status=1 ORDER BY {order} LIMIT 1"
        ).fetchone()
        if row:
            tok_id, tok = row[0], row[1]
    except Exception:
        pass
    con.close()
    if not tok:
        return [{"ok": False, "error": "no token"}]
    results = []
    for model in ("glm-5.2", "glm-5.2[1M]", "claude-haiku-4-5-20251001", "claude-opus-5[1M]"):
        body = json.dumps(
            {
                "model": model,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "stream": False,
            }
        ).encode()
        req = urllib.request.Request(
            GATEWAY + "/v1/messages",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {tok}",
                "anthropic-version": "2023-06-01",
            },
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()
                code = resp.status
            data = json.loads(raw.decode("utf-8", "replace"))
            text = "".join(
                b.get("text", "")
                for b in (data.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
            results.append(
                {
                    "model": model,
                    "ok": code == 200 and bool(text.strip()),
                    "code": code,
                    "ms": int((time.time() - t0) * 1000),
                    "text": (text or "")[:32],
                }
            )
        except Exception as e:
            results.append(
                {
                    "model": model,
                    "ok": False,
                    "error": type(e).__name__,
                    "ms": int((time.time() - t0) * 1000),
                }
            )
    for r in results:
        r["token_id"] = tok_id
    return results


def render_report(payload: dict) -> str:
    lines = [
        f"# NewAPI DX ops report — {payload['ts']}",
        "",
        f"dry_run: {payload.get('dry_run')}",
        "",
        "## Soft truncation",
        "```json",
        json.dumps(payload["soft"], ensure_ascii=False, indent=2),
        "```",
        f"decision: {payload.get('soft_decision')}",
        f"apply: {json.dumps(payload.get('soft_apply'), ensure_ascii=False)}",
        "",
        "## Opus latency",
        "```json",
        json.dumps(
            {k: v for k, v in payload["latency"].items() if k != "_meta"},
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## Weights",
        f"previous: {json.dumps(payload.get('weights_before'), ensure_ascii=False)}",
        f"suggested: {json.dumps(payload.get('weights_suggested'), ensure_ascii=False)}",
        f"apply: {json.dumps(payload.get('weights_apply'), ensure_ascii=False)}",
        "",
        "## Escalate",
        *[f"- {e}" for e in payload.get("escalate") or ["(none)"]],
        "",
        "## Smoke",
        "```json",
        json.dumps(payload.get("smoke"), ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def append_status(summary: str) -> None:
    if not STATUS_MD.exists():
        return
    block = f"\n\n## Changes ({datetime.now().strftime('%Y-%m-%d %H:%M')}) DX ops loop\n\n{summary}\n"
    STATUS_MD.write_text(STATUS_MD.read_text(encoding="utf-8") + block, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookback-hours", type=int, default=LOOKBACK_H)
    ap.add_argument("--skip-smoke", action="store_true")
    args = ap.parse_args()

    # single-instance lock (VPS/Linux only; --help exits during parse_args above)
    import fcntl

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another analyze_newapi_dx instance holds {LOCK_FILE} — abort")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    st = load_state()
    escalate: List[str] = []

    soft = collect_soft_journal(args.lookback_hours)
    if soft.get("error"):
        escalate.append(str(soft["error"]))
    latency = collect_opus_latency(args.lookback_hours)
    weights_before = current_weights()
    env = read_env_file()

    new_env, soft_esc, soft_decision = decide_soft_tuning(soft, latency, env)
    escalate.extend(soft_esc)
    soft_apply = {"applied": False, "skipped": True}
    if new_env and not args.dry_run:
        soft_apply = write_env_file(new_env, dry_run=False)
    elif new_env and args.dry_run:
        soft_apply = write_env_file(new_env, dry_run=True)
    elif not new_env:
        soft_apply = {"applied": False, "reason": soft_decision}

    suggested, w_esc = suggest_weights(latency, weights_before)
    escalate.extend(w_esc)
    force_demote = needs_force_demote(latency, weights_before, suggested)
    if force_demote:
        escalate.append("FORCE_DEMOTE: bypass weight cooldown (stall latency)")

    # cooldown for weights (skipped on force demote)
    last_w = st.get("last_weight_apply_ts") or 0
    weights_apply: Dict[str, Any]
    if args.dry_run:
        weights_apply = apply_weights(suggested, dry_run=True)
    elif (
        not force_demote
        and time.time() - last_w < WEIGHT_COOLDOWN_S
        and {int(k): v for k, v in st.get("last_suggested", {}).items()} == suggested
    ):
        escalate.append("weight cooldown active / unchanged suggestion — skip write")
        weights_apply = {"applied": False, "reason": "cooldown", "suggested": suggested}
    else:
        # only apply if any in-band change and enough channels have samples
        # (force demote may apply with fewer samples)
        sampled = sum(
            1
            for cid in OPUS_POOL
            if (latency.get(cid) or {}).get("n", 0) >= MIN_SAMPLES
            and weights_before.get(cid, {}).get("status") == 1
        )
        if sampled < 2 and not force_demote:
            escalate.append(f"weight apply skipped: sampled_channels={sampled}<2")
            weights_apply = {"applied": False, "reason": "low_coverage", "suggested": suggested}
        else:
            weights_apply = apply_weights(suggested, dry_run=False)
            if weights_apply.get("applied"):
                st["last_weight_apply_ts"] = time.time()
                st["last_suggested"] = suggested

    if soft_apply.get("unit_restart_failed"):
        escalate.append(f"guard 单元串行重启中止: {soft_apply['unit_restart_failed']}")
    if weights_apply.get("restart_error"):
        escalate.append(f"podman restart new-api 失败（restarted=False）: {weights_apply['restart_error']}")

    smoke_res = [] if (args.skip_smoke or args.dry_run) else smoke()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {
        "ts": ts,
        "dry_run": args.dry_run,
        "soft": soft,
        "soft_decision": soft_decision,
        "soft_apply": soft_apply,
        "latency": latency,
        "weights_before": {str(k): v for k, v in weights_before.items()},
        "weights_suggested": {str(k): v for k, v in suggested.items()},
        "weights_apply": weights_apply,
        "escalate": escalate,
        "smoke": smoke_res,
    }
    report_md = REPORT_DIR / f"dx-{ts}.md"
    report_json = REPORT_DIR / f"dx-{ts}.json"
    report_md.write_text(render_report(payload), encoding="utf-8")
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_bits = [
        f"report `{report_md}`",
        f"soft: {soft_decision}",
        f"soft_apply={soft_apply.get('applied')}",
        f"weights_apply={weights_apply.get('applied')}",
        f"escalate={len(escalate)}",
        f"smoke_ok={all(x.get('ok') for x in smoke_res) if smoke_res else 'skipped'}",
    ]
    if not args.dry_run:
        append_status("- " + "; ".join(summary_bits))
        save_state(st)

    print(json.dumps({"report": str(report_md), "summary": summary_bits, "escalate": escalate, "smoke": smoke_res, "weights_apply": weights_apply, "soft_apply": soft_apply}, ensure_ascii=False, indent=2))


def _write_failure_report(err: BaseException) -> None:
    """Best-effort failure report when main() blows up (e.g. sh() timeout/rc)."""
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        (REPORT_DIR / f"dx-{ts}-FAILED.md").write_text(
            f"# DX ops FAILED — {ts}\n\n```\n{type(err).__name__}: {err}\n```\n",
            encoding="utf-8",
        )
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _write_failure_report(e)
        raise RuntimeError(f"analyze_newapi_dx failed: {e}") from e
