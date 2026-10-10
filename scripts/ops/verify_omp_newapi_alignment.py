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
        problems += len(orphans)
        print(f"{provider} ({base}): {len(ids)} ids, {len(orphans)} without an enabled route")
        for i in orphans:
            if i in dark:
                chans = ", ".join(f"{c}({STATUS_NAMES.get(status.get(int(c[2:])), '?')})"
                                  for c in sorted(dark[i], key=lambda x: int(x[2:])))
                print(f"  ORPHAN {i}: every abilities row disabled -> {chans}")
            else:
                print(f"  ORPHAN {i}: NewAPI has no abilities row for this name at all")
    print(f"verdict: {'CLEAN' if not problems else f'{problems} orphan id(s) offered by OMP'}")
    return 0 if not problems else 1


STATUS_NAMES = {0: "disabled", 1: "enabled", 2: "manually-disabled", 3: "no-model"}


if __name__ == "__main__":
    sys.exit(main())
