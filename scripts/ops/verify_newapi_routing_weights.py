#!/usr/bin/env python3
"""Read-only audit: are the routing weights NewAPI actually uses the ones the UI shows?

Two tables hold a weight here. `channels.weight/priority` is what the console
edits; `abilities.weight/priority` is what the router selects from. They are only
kept in sync when a channel is written through the API, so any direct SQL change
that missed the abilities side leaves a channel whose displayed weight is a lie
while traffic keeps flowing on the stale value. That is invisible in the UI and
shows up as a pool that refuses to rebalance.

Checks, all against one read-only snapshot:
  A weight_drift      abilities row disagrees with its channel on weight/priority
  B ghost_route       abilities.enabled=1 on a channel that is not enabled
  C disabled_route    abilities.enabled=0 on an enabled channel (silent starvation)
  D orphan_ability    abilities.channel_id has no channel row
  E duplicate_ability same group/model/channel more than once
  F dead_names        a model with zero enabled routes in any group it appears in
  G ratio_gaps        a routable model missing from ModelRatio/CompletionRatio
  H bridge            the WorkBuddy channel and its three models, in detail

Nothing is written. Exit 0 only when A-E are clean; F and G are reported as
findings because they can be a deliberate disabled pool.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

CHANNEL_ENABLED = 1
STATUS_NAMES = {0: "disabled", 1: "enabled", 2: "manually-disabled", 3: "type-no-model"}


def db_path() -> Path:
    return Path.home() / ".new-api-local" / "new-api.db"


def option_value(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM options WHERE key = ?", (key,)).fetchone()
    return (row[0] if row else "") or ""


def audit(path: Path, bridge_name: str) -> tuple[list[str], list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        channels = {r[0]: r for r in conn.execute(
            'SELECT id, name, status, weight, priority, "group", models FROM channels')}
        abilities = list(conn.execute(
            'SELECT rowid, "group", model, channel_id, enabled, priority, weight '
            "FROM abilities"))

        counts: dict[tuple, int] = {}
        seen_model: dict[str, int] = {}
        for rid, group, model, cid, enabled, priority, weight in abilities:
            counts[(group, model, cid)] = counts.get((group, model, cid), 0) + 1
            ch = channels.get(cid)
            if ch is None:
                hard.append(f"D orphan_ability rowid={rid} {group}/{model} -> missing ch{cid}")
                continue
            ch_id, ch_name, ch_status, ch_weight, ch_priority = ch[0], ch[1], ch[2], ch[3], ch[4]
            if (weight, priority) != (ch_weight, ch_priority):
                hard.append(f"A weight_drift {group}/{model} ch{cid}({ch_name}): "
                            f"abilities w={weight} p={priority} vs channels w={ch_weight} "
                            f"p={ch_priority}")
            if enabled and ch_status != CHANNEL_ENABLED:
                hard.append(f"B ghost_route {group}/{model} ch{cid}({ch_name}) enabled but "
                            f"channel status={ch_status} ({STATUS_NAMES.get(ch_status, '?')})")
            if not enabled and ch_status == CHANNEL_ENABLED:
                hard.append(f"C disabled_route {group}/{model} ch{cid}({ch_name}) ability off "
                            "but channel enabled")
            seen_model[model] = max(seen_model.get(model, 0), int(bool(enabled)))

        for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if n > 1:
                hard.append(f"E duplicate_ability group={key[0]} model={key[1]} "
                            f"ch{key[2]} appears {n} times")

        dead = sorted(m for m, any_on in seen_model.items() if not any_on)
        if dead:
            soft.append(f"F dead_names ({len(dead)}): {', '.join(dead[:12])}"
                        + (" ..." if len(dead) > 12 else ""))

        ratios = {}
        for opt in ("ModelRatio", "CompletionRatio"):
            raw = option_value(conn, opt)
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                hard.append(f"G ratio_gaps: {opt} is not valid JSON")
                parsed = {}
            ratios[opt] = parsed if isinstance(parsed, dict) else {}
        routable = sorted(m for m, on in seen_model.items() if on)
        for opt in ("ModelRatio", "CompletionRatio"):
            missing = [m for m in routable if m not in ratios[opt]]
            if missing:
                soft.append(f"G ratio_gaps {opt}: {len(missing)} routable model(s) absent -> "
                            f"{', '.join(missing[:10])}" + (" ..." if len(missing) > 10 else ""))

        bridge = [c for c in channels.values() if c[1] == bridge_name]
        if bridge:
            b = bridge[0]
            soft.append(f"H bridge {bridge_name}: ch{b[0]} status={b[2]} "
                        f"({STATUS_NAMES.get(b[2], '?')}) w={b[3]} p={b[4]} group={b[5]} "
                        f"models={b[6]}")
            for rid, group, model, cid, enabled, priority, weight in abilities:
                if cid == b[0]:
                    soft.append(f"   ability {group}/{model}: enabled={enabled} "
                                f"w={weight} p={priority}")
    return hard, soft, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="override new-api.db path")
    parser.add_argument("--bridge-name", default="workbuddy-local-bridge")
    parser.add_argument("--quiet", action="store_true", help="only print the verdict lines")
    args = parser.parse_args()

    path = Path(args.db).resolve() if args.db else db_path()
    if not path.exists():
        print(f"FAIL: {path} not found")
        return 2
    hard, soft, _ = audit(path, args.bridge_name)
    for line in soft:
        print(f"note  {line}")
    for line in hard:
        print(f"ATTN  {line}")
    print(f"verdict: {'CLEAN' if not hard else f'{len(hard)} inconsistency(ies)'}; "
          f"{len(soft)} finding(s) reported")
    return 0 if not hard else 1


if __name__ == "__main__":
    sys.exit(main())
