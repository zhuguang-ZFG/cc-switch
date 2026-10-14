#!/usr/bin/env python3
"""Cross-layer gate: every model OMP offers must have a live NewAPI route.

OMP reads ~/.omp/agent/models.yml at boot and NewAPI routes from its abilities
table. Nothing keeps the two in sync, so a name can sit in the picker while every
channel serving it is disabled -- the user selects it, the gateway 404s or silently
falls back, and the failure looks like a bad model rather than a stale registry.

Only providers whose baseUrl points at the local NewAPI are audited; agentrouter
and friends talk to their upstream directly and have no abilities rows.

Read-only. Exit 1 when any offered id has no enabled route.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import yaml

MODELS_YML = Path(r"C:/Users/zhugu/.omp/agent/models.yml")
NEWAPI_DB = Path(r"D:/zhugu-home/home/.new-api-local/new-api.db")
LOCAL = re.compile(r"127\.0\.0\.1:(3002|3003)\b")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yml", default=str(MODELS_YML))
    parser.add_argument("--db", default=str(NEWAPI_DB))
    parser.add_argument("--no-quarantine", action="store_true",
                        help="treat quarantined ids as orphans too (strict mode)")
    args = parser.parse_args()

    doc = yaml.safe_load(Path(args.yml).read_text(encoding="utf-8"))
    providers = (doc or {}).get("providers") or {}

    with closing(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)) as conn:
        live = defaultdict(list)
        dark = defaultdict(list)
        for model, cid, on in conn.execute(
                "SELECT model, channel_id, enabled FROM abilities"):
            (live if on else dark)[model].append(f"ch{cid}")
        status = dict(conn.execute("SELECT id, status FROM channels"))

    problems = 0
    for provider, cfg in providers.items():
        base = (cfg or {}).get("baseUrl") or ""
        if not LOCAL.search(base):
            print(f"skip   {provider} ({base}) -- not the local NewAPI")
            continue
        ids = [m["id"] for m in (cfg or {}).get("models") or []
               if isinstance(m, dict) and m.get("id")]
        orphans = [i for i in ids if not live.get(i)]
        held = [] if args.no_quarantine else [i for i in orphans if i in QUARANTINE]
        orphans = [i for i in orphans if i not in held]
        problems += len(orphans)
        print(f"{provider} ({base}): {len(ids)} ids, {len(orphans)} without an enabled route"
              + (f", {len(held)} quarantined" if held else ""))
        for i in held:
            print(f"  QUARANTINE {i}: every route dark, expected -- {QUARANTINE[i]}")
        for i in orphans:
            if i in dark:
                chans = ", ".join(f"{c}({STATUS_NAMES.get(status.get(int(c[2:])), '?')})"
                                  for c in sorted(dark[i], key=lambda x: int(x[2:])))
                print(f"  ORPHAN {i}: every abilities row disabled -> {chans}")
            else:
                print(f"  ORPHAN {i}: NewAPI has no abilities row for this name at all")
    print(f"verdict: {'CLEAN' if not problems else f'{problems} orphan id(s) offered by OMP'}")
    return 0 if not problems else 1


# Transient dark legs with known recovery conditions (ops-notes 2026-10-12) -- NOT
# r11-style never-existed orphans. models.yml entries stay so recovery needs no
# re-registration; delete each line once its id routes live again.
QUARANTINE = {
    "qwen3.7-plus": "ch203 weekly Clinepass quota resets ~2026-10-16",
    "step-5-preview": "ch184 Go endpoint 429 + ch205 step-plan credit empty",
    "step-3.5-flash": "ch205 step-plan credit empty; top-up then re-enable",
    "step-3.5-flash-2603": "ch205 step-plan credit empty; top-up then re-enable",
    "step-3.7-flash": "ch205 step-plan credit empty; top-up then re-enable",
    "muse-spark-1.3-contributor-free": "ch178 bridge CLI dead (clean-window 150s timeout x1, 502 x1); row intentionally removed from ch178 -- re-add on CLI recovery",
    "big-pickle": "ch178 bridge CLI dead (150s timeout x2, clean windows); rows removed -- re-add on CLI recovery",
    "space-bunny-free": "ch178 bridge CLI dead (150s timeout x2, clean windows); rows removed -- re-add on CLI recovery",
    "mimo-v2.6-flash-free": "ch178 bridge CLI dead (150s timeout x2); rows removed -- re-add on CLI recovery",
    "ling-3.1-flash-free": "ch178 bridge CLI dead (502 + 180s timeout); rows removed -- re-add on CLI recovery",
    "ling-3.0-flash-fin-free": "ch178 bridge CLI dead (180s timeout + retired from zen catalog); rows removed -- re-add on CLI recovery",
    "glm-5.2": "ch220 lucky0625 p-20 live since 2026-10-14 (recovered; sole live leg, RPM-contended)",
    "glm-5.3": "ch208 happycoding wallet depleted ($0.0248, pre-charge failures), manually disabled 2026-10-14; sole live leg; OMP plan/designer/task repointed to glm-5.2, advisor still dark pending user decision; re-enable on recharge",
    "gemini-3.8-flash-high": "ch222 (chengapi) upstream key expired/401 Unauthorized (2026-10-13); re-enable on fresh key",
    "codex-auto-review": "ch175 hubway manually-disabled 2026-10-13 12:48 (external operator; sole live leg) -- re-enable on hubway restore",
    "gemini-3.8-flash": "ch220 lucky0625 manually-disabled 2026-10-13 15:37 (external operator; last logs green) -- tiny falls to glm-5.3-flash; re-enable on ch220 restore",
}


STATUS_NAMES = {0: "disabled", 1: "enabled", 2: "manually-disabled", 3: "no-model"}


if __name__ == "__main__":
    sys.exit(main())
