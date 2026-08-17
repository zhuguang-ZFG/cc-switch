#!/usr/bin/env python3
"""Remove only the exact OMP fallback chain for the current default model."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


EXPECTED_SELECTOR = "zg-newapi/k3"
THINKING_SUFFIXES = frozenset(
    ("minimal", "low", "medium", "high", "xhigh", "max", "auto")
)


def base_selector(selector: str) -> str:
    base, separator, suffix = selector.rpartition(":")
    return base if separator and suffix in THINKING_SUFFIXES else selector


def model_roles(text: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    in_roles = False
    for line in text.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "modelRoles:":
            if in_roles:
                raise RuntimeError("duplicate modelRoles mapping")
            in_roles = True
            continue
        if in_roles and stripped and indent == 0:
            break
        if in_roles and indent == 2 and ":" in stripped:
            role, selector = stripped.split(":", 1)
            roles[role.strip()] = selector.strip().strip("\"'")
    return roles


def fallback_blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    in_retry = False
    in_chains = False
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "retry:" and indent == 0:
            if in_retry:
                raise RuntimeError("duplicate retry mapping")
            in_retry = True
            continue
        if in_retry and stripped and indent == 0:
            break
        if in_retry and stripped == "fallbackChains:" and indent == 2:
            if in_chains:
                raise RuntimeError("duplicate retry.fallbackChains mapping")
            in_chains = True
            continue
        if in_chains and stripped and indent <= 2:
            break
        if in_chains and indent == 4 and stripped.endswith(":"):
            starts.append((stripped[:-1].strip().strip("\"'"), index))

    blocks: dict[str, tuple[int, int]] = {}
    for position, (key, start) in enumerate(starts):
        if key in blocks:
            raise RuntimeError(f"duplicate fallback chain {key!r}")
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        if position + 1 == len(starts):
            for index in range(start + 1, len(lines)):
                line = lines[index]
                if line.strip() and len(line) - len(line.lstrip(" ")) <= 2:
                    end = index
                    break
        blocks[key] = (start, end)
    return blocks


def remove_exact_default_chain(
    text: str, expected_selector: str = EXPECTED_SELECTOR
) -> tuple[str, str, bool]:
    roles = model_roles(text)
    selector = base_selector(roles.get("default", ""))
    if selector != expected_selector:
        raise RuntimeError(
            f"modelRoles.default is {selector!r}, expected {expected_selector!r}"
        )
    lines = text.splitlines(keepends=True)
    blocks = fallback_blocks(lines)
    target = blocks.get(expected_selector)
    if target is None:
        return text, selector, False
    start, end = target
    return "".join([*lines[:start], *lines[end:]]), selector, True


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def run_validation() -> None:
    commands = (
        ("omp", "models"),
        (
            sys.executable,
            "-m",
            "unittest",
            "scripts.ops.test_omp_routes.OmpRouteGateTests."
            "test_default_role_has_no_model_fallback_chain",
        ),
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
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
    text = original.decode("utf-8")
    updated, selector, changed = remove_exact_default_chain(text)
    print(f"default selector={selector}; exact fallback present={changed}")
    if not changed:
        print("already configured")
        return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = path.with_name(
        f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}-before-default-fallback.bak"
    )
    if backup.exists():
        raise RuntimeError(f"backup already exists: {backup}")
    backup.write_bytes(original)
    if backup.read_bytes() != original:
        raise RuntimeError("backup byte verification failed")

    try:
        atomic_write(path, updated.encode("utf-8"))
        readback = path.read_text(encoding="utf-8")
        verified, verified_selector, still_changed = remove_exact_default_chain(
            readback
        )
        if verified != readback or still_changed or verified_selector != selector:
            raise RuntimeError("fallback readback verification failed")
        if model_roles(readback) != model_roles(text):
            raise RuntimeError("modelRoles changed unexpectedly")
        run_validation()
    except BaseException:
        atomic_write(path, original)
        if path.read_bytes() != original:
            raise RuntimeError(f"rollback failed; backup={backup}")
        print(f"rollback restored original config; backup={backup.name}")
        raise

    print(f"OK: removed fallbackChains.{selector}; backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
