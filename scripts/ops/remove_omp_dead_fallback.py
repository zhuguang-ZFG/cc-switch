#!/usr/bin/env python3
"""Remove one known-unresolvable OMP fallback candidate with rollback."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


CHAIN = "zg-newapi/agnes-2.5-flash"
CANDIDATE = "zg-newapi/agnes-2.0-flash"


def remove_exact_candidate(
    text: str, chain: str = CHAIN, candidate: str = CANDIDATE
) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    in_retry = False
    seen_retry = False
    in_chains = False
    seen_chains = False
    active_chain: str | None = None
    matches: list[int] = []
    remaining = 0
    seen_chain = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "retry:" and indent == 0:
            if seen_retry:
                raise RuntimeError("duplicate retry mapping")
            in_retry = True
            seen_retry = True
            continue
        if in_retry and stripped and indent == 0:
            break
        if not in_retry:
            continue
        if stripped == "fallbackChains:" and indent == 2:
            if seen_chains:
                raise RuntimeError("duplicate retry.fallbackChains mapping")
            in_chains = True
            seen_chains = True
            continue
        if in_chains and stripped and indent <= 2:
            break
        if not in_chains:
            continue
        if indent == 4 and stripped.endswith(":"):
            active_chain = stripped[:-1].strip().strip("\"'")
            if active_chain == chain:
                if seen_chain:
                    raise RuntimeError(f"duplicate fallback chain {chain!r}")
                seen_chain = True
            continue
        if active_chain == chain and indent == 6 and stripped.startswith("- "):
            selector = stripped[2:].strip().strip("\"'")
            if selector == candidate:
                matches.append(index)
            else:
                remaining += 1

    if not seen_retry:
        raise RuntimeError("retry mapping not found")
    if not seen_chains:
        raise RuntimeError("retry.fallbackChains mapping not found")
    if not seen_chain:
        raise RuntimeError(f"fallback chain not found: {chain}")
    if len(matches) > 1:
        raise RuntimeError(f"duplicate fallback candidate: {candidate}")
    if not matches:
        return text, False
    if remaining == 0:
        raise RuntimeError(f"refusing to empty fallback chain: {chain}")
    del lines[matches[0]]
    return "".join(lines), True


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def validate(repo: Path) -> None:
    commands = (
        ("omp", "models"),
        (sys.executable, "-m", "unittest", "scripts.ops.test_omp_routes"),
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"validation failed: {' '.join(command[:3])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".omp" / "agent" / "config.yml",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    path = args.config.resolve()
    original = path.read_bytes()
    updated, changed = remove_exact_candidate(original.decode("utf-8"))
    print(f"chain={CHAIN}; deadCandidatePresent={str(changed).lower()}")
    if not changed:
        print("already configured")
        return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = path.with_name(
        f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}-before-dead-fallback.bak"
    )
    if backup.exists():
        raise RuntimeError(f"backup already exists: {backup}")
    backup.write_bytes(original)
    if backup.read_bytes() != original:
        raise RuntimeError("backup byte verification failed")
    try:
        atomic_write(path, updated.encode("utf-8"))
        verified, still_changed = remove_exact_candidate(path.read_text(encoding="utf-8"))
        if still_changed or verified != path.read_text(encoding="utf-8"):
            raise RuntimeError("fallback readback verification failed")
        validate(Path(__file__).resolve().parents[2])
    except BaseException:
        atomic_write(path, original)
        if path.read_bytes() != original:
            raise RuntimeError(f"rollback failed; backup={backup}")
        print(f"rollback restored original config; backup={backup.name}")
        raise

    print(f"OK: removed {CANDIDATE}; backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
