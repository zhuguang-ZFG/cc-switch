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
    "step-5-preview": "ch184 Go endpoint 429 + ch205/ch224 step-plan credit empty (ch224 parked 2026-10-15)",
    "step-3.5-flash": "ch205/ch224 step-plan credit empty (ch224 parked 2026-10-15); top-up then re-enable",
    "step-3.5-flash-2603": "ch205/ch224 step-plan credit empty (ch224 parked 2026-10-15); top-up then re-enable",
    "step-3.7-flash": "ch205/ch224 step-plan credit empty (ch224 parked 2026-10-15); top-up then re-enable",
    "qwen3.8-max": "ch118 upstream 500 do-request-failed x2 in clean windows (2026-10-15); other legs ch192/ch203/ch223 long-disabled; vision chain hop replaced; re-enable on seeseed recovery",
    "qwen3.7-max": "ch118 upstream 500 do-request-failed x2 (2026-10-15); ch89/ch203/ch223 long-disabled; plan-chain hop replaced; re-enable on seeseed recovery",
    "grok-4.5": "ch118 upstream 500 do-request-failed x2 (2026-10-15) + ch39 quarantined recovery point (status=2) + ch109 disabled; re-enable when either leg returns",
    "gpt-5.5": "upstream auth_unavailable 'gpt-5.5 is not supported when using Codex' on all four jojatoken channels (2026-10-15 probe x2); former midjok legs ch193/194 died on disabled API keys; compaction candidate replaced",
    "qwen3.8-flash": "ch186 tierflow parked 2026-10-15 (403 subscription quota insufficient, bucket=basic); ch122 long-disabled; re-enable on subscription bucket restore",
    "ling-3.1-flash-free": "retired from the live zen catalog (`opencode models` 2026-10-15) -- no re-add path via ch178; drop the models.yml row if a clean picker is wanted",
    "ling-3.0-flash-fin-free": "retired from the live zen catalog (2026-10-15, was 180s timeout + catalog retirement); no re-add path; drop the models.yml row if a clean picker is wanted",
    "glm-5.2": "ch220 lucky0625 account negative balance (-$0.000278), 403 insufficient quota account-wide (2026-10-15); all legs dark; OMP plan/designer/task repointed to k3; re-enable on ch220 recharge",
    "gemini-3.8-flash": "ch220 lucky0625 wallet negative (-$0.000278) -> account-wide 403; channel disabled 2026-10-13 15:37; tiny repointed to glm-5.3-flash; re-enable on ch220 recharge",
    "agnes-2.5-flash": "ch69 account restricted 403 anomalous-activity + ch68 upstream dead + ch220 broke (2026-10-15 admin tests); all legs dark",
    "claude-haiku-4-5": "ch68 upstream dead + ch69 account restricted + ch177 disabled (2026-10-15 admin tests); all legs dark",
    "mimo-v2.5": "ch109 model removed from plan 400 + ch111 balance=0 + clinepass quota (2026-10-15 admin tests); all legs dark",
    "grok-4.20-0309-reasoning": "ch109 imagic disabled + ch220 account negative balance (2026-10-15); all legs dark",
    "grok-4.20-0309-non-reasoning": "ch109 imagic disabled + ch220 account negative balance (2026-10-15); all legs dark",
    "grok-4.20-multi-agent-0309": "ch109 imagic disabled + ch220 account negative balance (2026-10-15); all legs dark",
    "grok-build-0.1": "ch109 imagic disabled + ch220 account negative balance (2026-10-15); all legs dark",
    "grok-4.3": "ch109 imagic disabled (2026-10-15); sole leg dark",
    "grok-composer-2.5-fast": "ch109 imagic disabled + ch220 account negative balance (2026-10-15); all legs dark",
    "muse-spark-1.2-contributor": "ch109 imagic disabled (2026-10-15); sole paid leg dark (free twin muse-spark-1.2-contributor-free is live on ch178, 2026-10-15)",
    "LongCat-2.0": "ch68 upstream dead + ch69 account restricted (2026-10-15 admin tests); all legs dark (LongCat-2.5-Preview ch145 unaffected)",
    "gemini-3.8-flash-high": "ch222 (chengapi) upstream key expired/401 Unauthorized (2026-10-13); re-enable on fresh key",
    "codex-auto-review": "ch175 hubway manually-disabled 2026-10-13 12:48 (external operator; sole live leg) -- re-enable on hubway restore",
    "Atria-Dawn-Preview": "ch139 upstream free credit exhausted: 429 Token quota exhausted (admin test 2026-10-15 HTTP 200 success=false; guardian stopped recovery probing after 12 failed cycles) -- re-enable only if the upstream credit resets",
}


STATUS_NAMES = {0: "disabled", 1: "enabled", 2: "manually-disabled", 3: "no-model"}


if __name__ == "__main__":
    sys.exit(main())
