#!/usr/bin/env python3
"""Register WorkBuddy's bridged models in the OMP model registry (models.yml).

Prerequisite: add_workbuddy_channel.py --apply has already put the -wb names on
the local NewAPI. OMP talks to zg-newapi at http://127.0.0.1:3002/v1, so an id
registered here with no matching NewAPI ability is a button that 404s.

Only providers.zg-newapi.models is touched. The file carries YAML anchors
(*id002) and per-entry comments, so this edits by text insert and validates with
a full parse afterwards -- never by yaml.dump, which would flatten both.

Limits come from the WorkBuddy account catalog
(~/.workbuddy/cache/acc-product-config-v3.json), which is a cache, not a probe:
  hy3                in 192000 / out 64000  efforts low,high      credits x0.00
  hy4-preview-f      in 960000 / out 64000  effort high only      credits x0.00
  deepseek-v4.1-flash in 1000000 / out 128000 efforts low,high,max credits x0.11
hy4 and deepseek also expose larger context tiers (600k/960k, 600k/1M); OMP has
one contextWindow per model, so the catalog's own defaultLength (300000) is used
rather than the ceiling a request would have to opt into.
input is text-only: the catalog advertises image support, but the converter has
never been shown to carry an image part, and a wrong capability claim makes OMP
attach images that then fail downstream.

compactionModel points at zg-newapi/deepseek-v4-flash, the same cheap target every
other entry uses, so summarising a session never spends WorkBuddy plan credits.

Default is a dry run. --apply writes a .bak, verifies the backup by sha256,
inserts only ids that are missing, re-parses, and refuses the result unless every
pre-existing model and every other provider survived unchanged; on any failure it
restores the backup. Pick-up needs an OMP restart -- models.yml is read at boot.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import shutil
import sys
import time

import yaml

MODELS_YML = pathlib.Path(r"C:/Users/zhugu/.omp/agent/models.yml")
PROVIDER = "zg-newapi"

NEW_MODELS = [
    {
        "id": "hy3-wb",
        "name": "Hy3 (WorkBuddy builtin free tier, local bridge)",
        "reasoning": True,
        "efforts": ["low", "high"],
        "contextWindow": 192000,
        "maxTokens": 64000,
    },
    {
        "id": "hy4-wb",
        "name": "Hy4 preview (WorkBuddy builtin free tier, local bridge)",
        "reasoning": True,
        "efforts": ["high"],
        "contextWindow": 300000,
        "maxTokens": 64000,
    },
    {
        "id": "deepseek-v4.1-flash-wb",
        "name": "Deepseek V4.1 Flash (WorkBuddy builtin, local bridge)",
        "reasoning": True,
        "efforts": ["low", "high", "max"],
        "contextWindow": 300000,
        "maxTokens": 128000,
    },
]

COMPACTION_TARGET = "zg-newapi/deepseek-v4-flash"


def render(entry: dict) -> str:
    """One models.yml list item, matching the file's existing indentation style."""
    lines = [f"    - id: {entry['id']}",
             f"      name: {entry['name']}",
             f"      reasoning: {'true' if entry['reasoning'] else 'false'}",
             "      thinking:",
             "        mode: effort",
             "        efforts:"]
    lines += [f"        - {e}" for e in entry["efforts"]]
    lines += ["      input:", "      - text",
              f"      contextWindow: {entry['contextWindow']}",
              f"      maxTokens: {entry['maxTokens']}",
              f"      compactionModel: {COMPACTION_TARGET}"]
    return "\n".join(lines) + "\n"


def load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def ids_of(doc: dict, provider: str) -> list[str]:
    models = (doc.get("providers") or {}).get(provider, {}).get("models") or []
    return [m["id"] for m in models if isinstance(m, dict) and m.get("id")]


def provider_blocks(text: str) -> dict[str, tuple[int, int]]:
    """Map provider name -> (start, end) line indices, end exclusive."""
    heads = [(i, m.group(1)) for i, l in enumerate(text.splitlines())
             if (m := re.match(r"^  ([\w.\-]+):\s*$", l))]
    return {name: (start, heads[i + 1][0] if i + 1 < len(heads) else len(text.splitlines()))
            for i, (start, name) in enumerate(heads)}


def insert_point(text: str) -> int:
    """Line index of the next provider header, i.e. end of PROVIDER's model list."""
    blocks = provider_blocks(text)
    if PROVIDER not in blocks:
        raise RuntimeError(f"provider {PROVIDER} not found in {MODELS_YML}")
    return blocks[PROVIDER][1]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="modify models.yml; default is a read-only dry run")
    parser.add_argument("--check", action="store_true",
                        help="only report which of the ids are already registered")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not MODELS_YML.exists():
        print(f"FAIL: {MODELS_YML} missing; is OMP installed?")
        return 1

    # This file is CRLF. read_text would hide that and a plain write would then
    # silently normalize all 1100+ lines to LF -- a whole-file diff for a
    # three-model insert. Read the bytes, keep the dominant ending, and prove the
    # result is original + block before calling it applied.
    original_bytes = MODELS_YML.read_bytes()
    newline = "\r\n" if b"\r\n" in original_bytes else "\n"
    text = original_bytes.decode("utf-8").replace("\r\n", "\n")
    doc = yaml.safe_load(text)
    existing = ids_of(doc, PROVIDER)
    missing = [e for e in NEW_MODELS if e["id"] not in existing]

    if args.check:
        for entry in NEW_MODELS:
            state = "registered" if entry["id"] in existing else "absent"
            print(f"  {entry['id']}: {state}")
        print(f"{PROVIDER} has {len(existing)} models; "
              f"{len(NEW_MODELS) - len(missing)}/{len(NEW_MODELS)} of the bridge ids present")
        return 0

    print(f"{MODELS_YML.name}: {len(text.splitlines())} lines, "
          f"{len(existing)} models under {PROVIDER}")
    if not missing:
        print("nothing to do: all bridge ids already registered")
        return 0

    block = "".join(render(e) for e in missing)
    print(f"would add {len(missing)}: {[e['id'] for e in missing]}")
    print("--- block ---")
    print(block.rstrip())
    print("--- end ---")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = MODELS_YML.with_suffix(f".yml.bak-{stamp}-wb")
    shutil.copy2(MODELS_YML, backup)
    before = sha256(MODELS_YML)
    if sha256(backup) != before:
        raise RuntimeError(f"backup {backup.name} does not match the original; aborting")
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, sha256={before[:12]})")

    at = insert_point(text)
    lines = text.splitlines(keepends=True)
    if not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    merged = "".join(lines[:at]) + block + "".join(lines[at:])
    payload = merged.replace("\n", newline).encode("utf-8")
    # Byte-level proof that nothing but the block changed: deleting the inserted
    # bytes must reproduce the original file exactly.
    if payload.replace(block.replace("\n", newline).encode("utf-8"), b"", 1) != original_bytes:
        raise RuntimeError("refusing to write: result is not original + inserted block")

    try:
        MODELS_YML.write_bytes(payload)
        after_doc = load(MODELS_YML)
    except Exception as error:
        shutil.copy2(backup, MODELS_YML)
        raise RuntimeError(f"write/parse failed, restored {backup.name}: {error}") from error

    after_ids = ids_of(after_doc, PROVIDER)
    lost = [m for m in existing if m not in after_ids]
    if lost:
        shutil.copy2(backup, MODELS_YML)
        raise RuntimeError(f"pre-existing models vanished {lost}; restored {backup.name}")
    for entry in missing:
        if entry["id"] not in after_ids:
            shutil.copy2(backup, MODELS_YML)
            raise RuntimeError(f"{entry['id']} not readable after insert; restored {backup.name}")
    for provider in (doc.get("providers") or {}):
        if provider == PROVIDER:
            continue
        if ids_of(doc, provider) != ids_of(after_doc, provider):
            shutil.copy2(backup, MODELS_YML)
            raise RuntimeError(f"provider {provider} changed; restored {backup.name}")
    if len(after_ids) != len(existing) + len(missing):
        shutil.copy2(backup, MODELS_YML)
        raise RuntimeError("model count did not grow by exactly the inserted ids; "
                           f"restored {backup.name}")

    written = MODELS_YML.read_text(encoding="utf-8")
    if "\t" in written:
        shutil.copy2(backup, MODELS_YML)
        raise RuntimeError(f"tab in output; restored {backup.name}")
    print(f"OK: {PROVIDER} now has {len(after_ids)} models. Backup: {backup.name}")
    print("restart OMP (models.yml is read at boot), then select one of "
          f"{[e['id'] for e in missing]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
