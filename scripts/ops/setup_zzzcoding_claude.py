#!/usr/bin/env python3
"""Recreate ~/.claude/zzzcoding-settings.json for direct Claude Code access to api.zzzcoding.org.

The key is NOT stored in the repo. It is taken from:
  1. ZZ_KEY env var, or
  2. first command-line argument.

Usage:
  python3 scripts/ops/setup_zzzcoding_claude.py            # requires ZZ_KEY env var
  python3 scripts/ops/setup_zzzcoding_claude.py sk-xxxx    # key as argument
"""
import json
import os
import sys
import time


def main() -> int:
    key = os.environ.get("ZZ_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    key = key.strip()
    if not key:
        print("ERROR: no key. Set ZZ_KEY env var or pass key as argument.", file=sys.stderr)
        return 1
    if not key.startswith("sk-"):
        print("WARNING: key does not start with sk-; proceeding anyway.", file=sys.stderr)

    settings_path = os.path.expanduser("~/.claude/zzzcoding-settings.json")
    if os.path.exists(settings_path):
        bak = settings_path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        os.replace(settings_path, bak)
        print("backed up existing settings ->", bak)

    settings = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://api.zzzcoding.org",
            "ANTHROPIC_API_KEY": key,
            "ANTHROPIC_AUTH_TOKEN": key,
            "DISABLE_TELEMETRY": "true",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        }
    }
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    print("written:", settings_path)
    print("launcher: ~/zzzcoding.cmd  (claude --settings <this file>)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
