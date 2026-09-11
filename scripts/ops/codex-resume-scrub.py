#!/usr/bin/env python3
"""Scrub reasoning response_items from a Codex rollout so a bricked session can resume.

Codex runs stateless (`disable_response_storage = true`): every request replays the
thread history including reasoning items (id + encrypted_content). Items minted by
one upstream account are rejected by any other account in the sharedchat/any pool
("encrypted content could not be verified" / "Item with id 'rs_...' not found").
Once such an item is in the rollout, every later turn fails. Removing the
reasoning items restores the thread; message and tool-call history is untouched.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    path = args.rollout.resolve()
    if not path.is_file():
        raise SystemExit(f"rollout not found: {path}")
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    counts: dict[str, int] = {}
    for line in raw_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise SystemExit(f"non-JSONL line encountered, aborting")
        if record.get("type") == "response_item" and record.get("payload", {}).get("type") == "reasoning":
            counts["reasoning_response_item"] = counts.get("reasoning_response_item", 0) + 1
            continue
        counts[record.get("type", "?")] = counts.get(record.get("type", "?"), 0) + 1
        kept.append(line)

    summary = {
        "rollout": str(path),
        "total_lines": len(raw_lines),
        "kept_lines": len(kept),
        "dropped": counts.get("reasoning_response_item", 0),
        "remaining_payload_types": counts,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if not args.apply:
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-reasoning-scrub-{stamp}")
    shutil.copy2(path, backup)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(json.dumps({"applied": True, "backup": str(backup)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
