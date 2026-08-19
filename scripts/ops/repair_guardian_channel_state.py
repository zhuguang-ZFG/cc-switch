#!/usr/bin/env python3
"""Remove selected channel recovery metadata from stopped Guardian state."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


STATE_KEYS = (
    "weight_history",
    "degraded_channels",
    "joined_channels",
    "channel_identities",
)


def remove_channel_state(state: dict, channel_ids: set[int]) -> tuple[dict, int]:
    updated = json.loads(json.dumps(state))
    removed = 0
    disabled = updated.get("disabled_channels")
    if isinstance(disabled, list):
        kept = [
            record
            for record in disabled
            if not isinstance(record, dict) or record.get("id") not in channel_ids
        ]
        removed += len(disabled) - len(kept)
        updated["disabled_channels"] = kept
    for key in STATE_KEYS:
        entries = updated.get(key)
        if not isinstance(entries, dict):
            continue
        for channel_id in channel_ids:
            removed += int(entries.pop(str(channel_id), None) is not None)
    return updated, removed


def atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def restore_originals(originals: dict[Path, bytes | None]) -> None:
    for path, content in originals.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel_ids", nargs="+", type=int)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".omp" / "guardian" / "state.json",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    channel_ids = set(args.channel_ids)
    if any(channel_id <= 0 for channel_id in channel_ids):
        parser.error("channel ids must be positive")

    state_path = args.state.resolve()
    backup_path = state_path.with_name("state.json.last-good")
    original = state_path.read_bytes()
    state = json.loads(original.decode("utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError("state root is not an object")
    updated, removed = remove_channel_state(state, channel_ids)
    payload = (json.dumps(updated, indent=2) + "\n").encode("utf-8")
    print(f"channels={sorted(channel_ids)} entriesToRemove={removed}")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    originals = {
        state_path: original,
        backup_path: backup_path.read_bytes() if backup_path.exists() else None,
    }
    backup_payloads = [
        (
            path.with_name(
                f"{path.name}.{stamp}-before-channel-state-repair.bak"
            ),
            content,
        )
        for path, content in originals.items()
        if content is not None
    ]
    collisions = [backup for backup, _ in backup_payloads if backup.exists()]
    if collisions:
        raise RuntimeError(f"backup already exists: {collisions[0]}")

    backups: list[Path] = []
    for backup, content in backup_payloads:
        assert content is not None
        with backup.open("xb") as handle:
            handle.write(content)
        if backup.read_bytes() != content:
            raise RuntimeError(f"backup verification failed: {backup}")
        backups.append(backup)

    try:
        atomic_write(state_path, payload)
        atomic_write(backup_path, payload)
        if state_path.read_bytes() != payload or backup_path.read_bytes() != payload:
            raise RuntimeError("state readback verification failed")
    except BaseException:
        restore_originals(originals)
        raise

    print(f"OK: removed={removed} backups={len(backups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
