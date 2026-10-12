#!/usr/bin/env python3
"""Ration the Kimi subscription: one human entry point, no automatic draws.

Why this exists (measured on the local gateway, 2026-10-11):

* ch33 ``kimi-official-k3`` is a **subscription** leg, not metered API. Its whole
  history is ``8398`` requests carrying ``1,094,938,913`` input tokens -- an
  average of ``~130k`` input tokens per request. On 2026-10-04/05/06 it served
  1300-1900 requests a day and then went silent after 2026-10-09 02:54, which is
  what a drained subscription window looks like.
* The burner was gateway token id=6 (``local-windows-clients``), since deleted.
  OMP and qodercli both authenticate as token id=1 (``master``, group
  ``default``), so token-level limits cannot tell the two clients apart; the only
  usable lever is which model **names** each client is handed.
* Names that reach a Kimi subscription leg: ``k3`` / ``k3-256k`` /
  ``kimi-for-coding`` / ``kimi-for-coding-highspeed`` -> ch33 only.
  ``kimi-k3`` -> ch208 happycoding (p30) + ch203 clinepass (p10, weekly-capped
  until ~2026-10-16); ``kimi-k3-oc`` -> ch202 (key expired 2026-10-10);
  ``kimi-k3-ark`` -> ch210 (parked).
* qodercli's background automation runs as its own bundled ``qfmodel``, and OMP's
  ``compactionModel`` is ``zg-newapi/deepseek-v4-flash``, so neither spends Kimi
  quota on summarising. The exposure is selection, which is why the fix is names.

Policy applied here, chosen by the operator:

* qodercli ``providers.newapi-local.models`` keeps exactly **one** Kimi selector,
  ``k3``. Extra aliases are removed because every extra name is another way to
  double-dip the same subscription, and ``kimi-k3`` would route to a different
  subscription pool whose failover leg is capped.
* OMP ``models.yml`` loses the ``kimi-k3-oc`` selector: its key expired
  2026-10-10 15:11, so it is a button that only 401s, and leaving a kimi id
  registered invites someone to point a role at it once the key is refreshed.
* Neither the gateway (ch33 stays p50/w10 in group ``default``) nor Kimi Code's
  direct ``api.kimi.com/coding`` path is touched -- the operator keeps Kimi Code as
  the intended consumer of the subscription.

Transport invariants this gate refuses to write without:

* ``providers.newapi-local.baseUrl`` must end in ``/v1``. qodercli appends
  ``/chat/completions`` verbatim; without the suffix it hits the SPA catch-all,
  gets HTTP 200 ``text/html``, and reports ``incomplete_stream`` -- which looks
  like a dead model but is a wiring bug.
* The wired name must have an enabled ability row in the token's own group, else
  the gateway sheds an unmapped 404 that ``AutomaticRetryStatusCodes``
  (``408,500-503``) will not retry.
* ``apiKey`` values are never printed; providers are compared by key name.

Both files are edited by exact text surgery, never by a re-serializer: settings
formatting is preserved and models.yml is CRLF with anchors and comments that
``yaml.dump`` would flatten. Each write is proven at byte level (deleting the
removed block must reproduce the new file), sha256-backed up, re-parsed, and is
auto-restored if verification fails. Pick-up needs a qodercli session restart and
an OMP restart (models.yml is read at boot).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import yaml

ENTRY_POINT = "k3"
KIMI_FAMILY = frozenset(
    {
        "k3",
        "k3-256k",
        "kimi-for-coding",
        "kimi-for-coding-highspeed",
        "kimi-k2.6",
        "kimi-k2.7-code",
        "kimi-k3",
        "kimi-k3-ark",
        "kimi-k3-oc",
    }
)
OMP_RETIRED = ("kimi-k3-oc",)
QODER_PROVIDER = "newapi-local"
MODELS_KEY = "models"
ENTRY_FIELD = "model"
TAG = "kimi-ration"


def settings_path() -> Path:
    return Path.home() / ".qoder" / "settings.json"


def models_yml_path() -> Path:
    return Path.home() / ".omp" / "agent" / "models.yml"


def gateway_db() -> Path:
    return Path.home() / ".new-api-local" / "new-api.db"


def fatal(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_literal(path: Path) -> str:
    # bytes, not read_text(): universal newlines would hide a CRLF file and the
    # rewrite would silently flatten every line to LF.
    text = path.read_bytes().decode("utf-8")
    if text.startswith("\ufeff"):
        fatal(f"{path} has a UTF-8 BOM; not a file this gate has written")
    return text


# ---------------------------------------------------------------- qodercli side


def qoder_current() -> tuple[dict, dict, list[str]]:
    text = read_literal(settings_path())
    data = json.loads(text)
    cfg = (data.get("providers") or {}).get(QODER_PROVIDER)
    if not isinstance(cfg, dict):
        fatal(f"providers.{QODER_PROVIDER} missing from {settings_path()}")
    if not str(cfg.get("baseUrl", "")).rstrip("/").endswith("/v1"):
        fatal(
            f"providers.{QODER_PROVIDER}.baseUrl={cfg.get('baseUrl')!r} lacks /v1; "
            "qodercli would append /chat/completions to the SPA catch-all"
        )
    models = cfg.get(MODELS_KEY)
    if not isinstance(models, list):
        fatal(f"providers.{QODER_PROVIDER}.{MODELS_KEY} is {type(models).__name__}")
    names = []
    for item in models:
        if not isinstance(item, dict) or not isinstance(item.get(ENTRY_FIELD), str):
            fatal(f"unexpected {MODELS_KEY} entry: {json.dumps(item, ensure_ascii=False)}")
        names.append(item[ENTRY_FIELD])
    return data, cfg, names


def qoder_token_groups(cfg: dict) -> list[str]:
    api_key = str(cfg.get("apiKey", ""))
    if not api_key:
        fatal(f"providers.{QODER_PROVIDER}.apiKey is empty")
    db = sqlite3.connect(f"file:{gateway_db()}?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT status, \"group\" FROM tokens WHERE key = ?",
            (api_key.removeprefix("sk-"),),
        ).fetchone()
    finally:
        db.close()
    if row is None:
        fatal(f"providers.{QODER_PROVIDER}.apiKey matches no gateway token")
    status, group = row
    if status != 1:
        fatal(f"gateway token status={status}, want 1 (enabled)")
    groups = [g.strip() for g in str(group or "").split(",") if g.strip()]
    if not groups:
        fatal("gateway token has no group; it can route nothing")
    return groups


def qoder_entry_point_live(groups: list[str]) -> str:
    db = sqlite3.connect(f"file:{gateway_db()}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT a.channel_id, c.name, a.enabled, a.priority, a.weight "
            "FROM abilities a JOIN channels c ON c.id = a.channel_id "
            "WHERE a.model = ? AND a.\"group\" IN ("
            + ",".join("?" for _ in groups)
            + ") ORDER BY a.priority DESC",
            (ENTRY_POINT, *groups),
        ).fetchall()
    finally:
        db.close()
    if not rows:
        fatal(f"{ENTRY_POINT} has no abilities row in token groups {groups}")
    live = [r for r in rows if r[2] == 1]
    if not live:
        fatal(f"{ENTRY_POINT} legs are all disabled: {live or rows}")
    return ", ".join(f"ch{r[0]}({r[1]},p{r[3]}w{r[4]})" for r in live)


def qoder_plan(names: list[str], groups: list[str]) -> tuple[list[str], list[str], bool, str]:
    """Return (non-kimi names, kimi names to drop, k3 to add, live legs)."""
    kept = [n for n in names if n not in KIMI_FAMILY]
    dropped = [n for n in names if n in KIMI_FAMILY and n != ENTRY_POINT]
    add = ENTRY_POINT not in names
    detail = qoder_entry_point_live(groups)
    return kept, dropped, add, detail


def qoder_write(backup: Path) -> tuple[str, str]:
    path = settings_path()
    original = read_literal(path)
    data, cfg, names = qoder_current()
    groups = qoder_token_groups(cfg)
    kept, dropped, add, detail = qoder_plan(names, groups)
    if not dropped and not add:
        return f"qodercli: already rationed to {ENTRY_POINT} ({detail})", ""

    indent = "  "
    match = re.search(r"\n([ \t]+)[^ \t\n]", original)
    if match:
        indent = match.group(1).replace("\t", "  ")
    eol = "\r\n" if "\r\n" in original else "\n"

    target = kept + [ENTRY_POINT]
    cfg[MODELS_KEY] = [{ENTRY_FIELD: n} for n in target]
    text = json.dumps(data, indent=indent, ensure_ascii=False).replace("\n", eol)
    if original.endswith("\n"):
        text += eol

    # One complete assertion: the new document must equal the old document with
    # exactly this provider's models list replaced. Anything else -- a renamed
    # key, a lost provider, a stray field -- fails here rather than on disk.
    expected = json.loads(original)
    expected["providers"][QODER_PROVIDER][MODELS_KEY] = [
        {ENTRY_FIELD: n} for n in target
    ]
    if json.loads(text) != expected:
        fatal("rewrite is not original-with-models-replaced; aborted")

    backup.write_bytes(path.read_bytes())
    if sha256(backup) != sha256(path):
        fatal(f"backup {backup.name} does not match the original; aborted before write")
    path.write_bytes(text.encode("utf-8"))

    reloaded = qoder_current()[2]
    kimi_left = [n for n in reloaded if n in KIMI_FAMILY]
    if kimi_left != [ENTRY_POINT]:
        restore_file(path, backup)
        fatal(f"qodercli still exposes {kimi_left}; restored {backup.name}")
    if len(reloaded) != len(set(reloaded)):
        restore_file(path, backup)
        fatal(f"duplicate entries after write: {reloaded}; restored {backup.name}")
    return (
        f"qodercli: dropped={dropped or '[]'} added={add} "
        f"exposure is now {ENTRY_POINT} only ({detail})",
        f"  backup={backup.name} ({backup.stat().st_size}B sha256={sha256(backup)[:12]})",
    )


# ---------------------------------------------------------------------- OMP side


def omp_blocks(text: str) -> dict[str, tuple[int, int]]:
    """provider name -> (start, end) line indices, end exclusive."""
    heads = [
        (i, m.group(1))
        for i, line in enumerate(text.splitlines())
        if (m := re.match(r"^  ([\w.\-]+):\s*$", line))
    ]
    return {
        name: (start, heads[i + 1][0] if i + 1 < len(heads) else len(text.splitlines()))
        for i, (start, name) in enumerate(heads)
    }


def omp_find_block(text: str, model_id: str) -> str:
    """Exact text of one `- id: <model_id>` list item, LF-normalized."""
    lines = text.splitlines()
    starts = [
        (i, line) for i, line in enumerate(lines) if line.startswith("    - id: ")
    ]
    for index, (start, line) in enumerate(starts):
        if line.strip() != f"- id: {model_id}":
            continue
        end = (
            starts[index + 1][0]
            if index + 1 < len(starts)
            else omp_next_provider_line(lines, start)
        )
        while end - 1 > start and not lines[end - 1].strip():
            end -= 1
        return "\n".join(lines[start:end]) + "\n"
    fatal(f"OMP selector {model_id!r} not found in {models_yml_path()}")
    return ""


def omp_next_provider_line(lines: list[str], start: int) -> int:
    for i in range(start + 1, len(lines)):
        if re.match(r"^(  \S|\S)", lines[i]):
            return i
    return len(lines)


def omp_referenced_in_config(model_id: str) -> list[str]:
    config = models_yml_path().with_name("config.yml")
    if not config.exists():
        return []
    hits = [
        f"{config.name}:{i}"
        for i, line in enumerate(config.read_text(encoding="utf-8").splitlines(), 1)
        if model_id in line
    ]
    return hits


def omp_plan(text: str) -> list[tuple[str, str]]:
    doc = yaml.safe_load(text)
    registered = {
        model["id"]
        for provider in (doc.get("providers") or {}).values()
        for model in (provider or {}).get("models") or []
        if isinstance(model, dict) and model.get("id")
    }
    plan = []
    for model_id in OMP_RETIRED:
        if model_id not in registered:
            plan.append((model_id, ""))
            continue
        block = omp_find_block(text, model_id)
        plan.append((model_id, block))
    return plan


def omp_write(backup: Path) -> tuple[str, str]:
    path = models_yml_path()
    original_bytes = path.read_bytes()
    newline = "\r\n" if b"\r\n" in original_bytes else "\n"
    text = original_bytes.decode("utf-8").replace("\r\n", "\n")
    doc = yaml.safe_load(text)
    registered = {
        model["id"]
        for provider in (doc.get("providers") or {}).values()
        for model in (provider or {}).get("models") or []
        if isinstance(model, dict) and model.get("id")
    }
    targets = [m for m in OMP_RETIRED if m in registered]
    if not targets:
        return f"OMP: already free of {list(OMP_RETIRED)}", ""

    merged = text
    blocks = []
    for model_id in targets:
        refs = omp_referenced_in_config(model_id)
        if refs:
            fatal(f"{model_id} is still referenced by config.yml at {refs}")
        block = omp_find_block(merged, model_id)
        if merged.count(block) != 1:
            fatal(f"{model_id} block is not unique ({merged.count(block)} matches)")
        merged = merged.replace(block, "", 1)
        blocks.append((model_id, block))

    payload = merged.replace("\n", newline).encode("utf-8")
    # Byte-level proof: deleting exactly these blocks from the original must
    # reproduce the payload, so nothing else in 1100+ lines moved.
    expected = original_bytes
    for _, block in blocks:
        raw = block.replace("\n", newline).encode("utf-8")
        if expected.count(raw) != 1:
            fatal(f"block bytes for {block.splitlines()[0]!r} not found once")
        expected = expected.replace(raw, b"", 1)
    if expected != payload:
        fatal("refusing to write: result is not original minus the retired blocks")

    backup.write_bytes(original_bytes)
    if sha256(backup) != sha256(path):
        fatal(f"backup {backup.name} does not match the original; aborted before write")
    path.write_bytes(payload)

    try:
        after = yaml.safe_load(path.read_bytes().decode("utf-8").replace("\r\n", "\n"))
    except Exception as error:
        restore_file(path, backup)
        fatal(f"OMP models.yml no longer parses ({error}); restored {backup.name}")

    after_ids = {
        model["id"]
        for provider in (after.get("providers") or {}).values()
        for model in (provider or {}).get("models") or []
        if isinstance(model, dict) and model.get("id")
    }
    lost = [m for m in registered if m not in after_ids and m not in targets]
    if lost:
        restore_file(path, backup)
        fatal(f"unrelated OMP models vanished {lost}; restored {backup.name}")
    leaked = [m for m in targets if m in after_ids]
    if leaked:
        restore_file(path, backup)
        fatal(f"{leaked} still registered after removal; restored {backup.name}")
    for provider, spec in (doc.get("providers") or {}).items():
        wanted = [m["id"] for m in (spec or {}).get("models") or [] if isinstance(m, dict)]
        got = [
            m["id"]
            for m in ((after.get("providers") or {}).get(provider) or {}).get("models") or []
            if isinstance(m, dict)
        ]
        expect = [m for m in wanted if m not in targets]
        if got != expect:
            restore_file(path, backup)
            fatal(f"provider {provider} model list changed unexpectedly; restored {backup.name}")
    return (
        f"OMP: removed selector(s) {targets} "
        f"({sum(len(b.splitlines()) for _, b in blocks)} lines)",
        f"  backup={backup.name} ({backup.stat().st_size}B sha256={sha256(backup)[:12]})",
    )


def restore_file(path: Path, backup: Path) -> None:
    shutil.copy2(backup, path)


def describe() -> int:
    _, q_cfg, q_names = qoder_current()
    groups = qoder_token_groups(q_cfg)
    kept, dropped, add, detail = qoder_plan(q_names, groups)
    print(f"qodercli providers.{QODER_PROVIDER} baseUrl={q_cfg['baseUrl']}")
    print(f"  token groups={groups} entry point {ENTRY_POINT} -> {detail}")
    print(f"  kimi names exposed={[n for n in q_names if n in KIMI_FAMILY]}")
    print(f"  would drop={dropped} add={add} non-kimi entries kept={len(kept)}")

    text = read_literal(models_yml_path()).replace("\r\n", "\n")
    doc = yaml.safe_load(text)
    registered = {
        model["id"]
        for provider in (doc.get("providers") or {}).values()
        for model in (provider or {}).get("models") or []
        if isinstance(model, dict) and model.get("id")
    }
    for model_id in OMP_RETIRED:
        if model_id not in registered:
            print(f"OMP {model_id}: absent")
            continue
        block = omp_find_block(text, model_id)
        refs = omp_referenced_in_config(model_id)
        print(
            f"OMP {model_id}: registered, {len(block.splitlines())} lines, "
            f"config.yml refs={refs or 'none'}"
        )
        print("  " + "\n  ".join(block.splitlines()))
    db = sqlite3.connect(f"file:{gateway_db()}?mode=ro", uri=True)
    try:
        ch = db.execute(
            "SELECT name, status, priority, weight, \"group\" FROM channels WHERE id = 33"
        ).fetchone()
    finally:
        db.close()
    print(
        "gateway untouched: "
        f"ch33 {ch[0]} status={ch[1]} p={ch[2]} w={ch[3]} group={ch[4]}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ration the Kimi subscription")
    parser.add_argument("--apply", action="store_true", help="write both config files")
    args = parser.parse_args()

    for path in (settings_path(), models_yml_path(), gateway_db()):
        if not path.exists():
            fatal(f"{path} not found")

    if not args.apply:
        return describe()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    first = qoder_write(settings_path().with_name(f"{settings_path().name}.bak-{stamp}-{TAG}"))
    second = omp_write(
        models_yml_path().with_name(f"{models_yml_path().name}.bak-{stamp}-{TAG}")
    )
    for line in (*first, *second):
        if line:
            print(line)
    print("restart qodercli session and OMP to pick this up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
