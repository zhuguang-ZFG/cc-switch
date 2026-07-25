#!/usr/bin/env python3
"""Cursor ops entry: SSH-run VPS NewAPI DX analyze loop and print summary.

Usage:
  python scripts/ops/newapi-dx-analyze.py
  python scripts/ops/newapi-dx-analyze.py --dry-run

Credentials: first line of D:\\Downloads\\VPS.txt (gitignored local note).
Schedule: Task Scheduler / Cursor loop daily ~09:00 calling this script.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VPS_TXT = Path(r"D:\Downloads\VPS.txt")
HOST = "47.112.162.80"
REMOTE = "/opt/new-api/analyze_newapi_dx.py"


def pwd() -> str:
    if not VPS_TXT.is_file():
        raise ValueError(f"missing credentials file: {VPS_TXT}")
    lines = VPS_TXT.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        raise ValueError(f"empty credentials file: {VPS_TXT}")
    m = re.search(r"密码[:：]\s*(\S+)", lines[0])
    if not m:
        raise ValueError(
            f"could not parse password from first line of {VPS_TXT} "
            "(expected …密码: <secret>)"
        )
    return m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-smoke", action="store_true")
    args = ap.parse_args()

    cmd = f"/usr/bin/python3 {REMOTE}"
    if args.dry_run:
        cmd += " --dry-run"
    if args.skip_smoke:
        cmd += " --skip-smoke"

    try:
        password = pwd()
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(
            HOST,
            username="root",
            password=password,
            timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as ex:
        print(f"SSH connect failed ({HOST}): {ex}", file=sys.stderr)
        return 1

    try:
        _, o, e = c.exec_command(cmd, timeout=300)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        status = o.channel.recv_exit_status()
    except Exception as ex:
        print(f"remote exec failed: {ex}", file=sys.stderr)
        return 1
    finally:
        c.close()

    sys.stdout.write(out)
    if err.strip():
        sys.stderr.write(err[:1000])
        if not err.endswith("\n"):
            sys.stderr.write("\n")
    return int(status)


if __name__ == "__main__":
    raise SystemExit(main())
