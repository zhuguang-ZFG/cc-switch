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
    line = VPS_TXT.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    return re.search(r"密码[:：]\s*(\S+)", line).group(1)


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

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        HOST,
        username="root",
        password=pwd(),
        timeout=20,
        allow_agent=False,
        look_for_keys=False,
    )
    _, o, e = c.exec_command(cmd, timeout=300)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    sys.stdout.write(out)
    if err.strip():
        sys.stderr.write(err[:1000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
