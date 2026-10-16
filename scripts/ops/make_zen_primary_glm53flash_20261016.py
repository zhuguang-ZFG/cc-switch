#!/usr/bin/env python3
"""Make the zen free bridge (ch178) the PRIMARY leg for `glm-5.3-flash`.

User intent (2026-10-16): "zen做主力" -- the free opencode zen route should
carry the main load for this id. OMP uses `zg-newapi/glm-5.3-flash` for the
`tiny` / `advisor` roles and the `smol` fallback chain (utility calls, no tool
use), so the free leg becomes the default carrier while the paid legs
(ch118 p25 / ch173 p20 / ch208 p5) stay as failover.

Change: ch178 gains `glm-5.3-flash` in its models with
model_mapping {"glm-5.3-flash": "glm-5.3-flash-free"} (the bridge only knows the
free id) and priority 0 -> 30 (above ch118's p25). Only one other id served by
ch178 has any competing leg (`longcat-2.5-preview-free` on ch133 p1), which the
elevation also makes zen-first -- consistent with the intent.

Safety: whole-DB snapshot, key hydrated from the SSOT (never from the masked
list payload), dry-run by default, readback of models/mapping/priority/
channel_info/key-hash, admin test, then a real gateway relay whose attribution
is confirmed against the `logs` table; any failure restores the original row.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CHANNEL_ID = 178
NEW_MODEL = "glm-5.3-flash"
UPSTREAM_MODEL = "glm-5.3-flash-free"
TARGET_PRIORITY = 30


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["newapi_local_smoke"] = module
    spec.loader.exec_module(module)
    return module


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = parser.parse_args()

    smoke = load_smoke()
    db_path = Path(smoke.DEPLOY_DIR) / "new-api.db"
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
        row = cn.execute(
            "SELECT key, models, coalesce(model_mapping,''), priority, weight, status,"
            " coalesce(channel_info,'') FROM channels WHERE id=?", (CHANNEL_ID,)).fetchone()
    ssot_key, models_raw, mapping_raw, priority, weight, status, channel_info = row
    models = [m.strip() for m in (models_raw or "").split(",") if m.strip()]
    mapping = json.loads(mapping_raw) if mapping_raw.strip() else {}
    print(f"ch{CHANNEL_ID}: {len(models)} models, priority={priority}, weight={weight}, "
          f"status={status}, key_sha={sha(ssot_key)} mapping_keys={list(mapping)}")
    if NEW_MODEL in models:
        print(f"{NEW_MODEL} already present on ch{CHANNEL_ID}; nothing to add")

    _, listing = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers)
    data = (listing or {}).get("data") or []
    items = (data.get("items") if isinstance(data, dict) else data) or []
    channel = next((c for c in items if int(c.get("id", -1)) == CHANNEL_ID), None)
    if channel is None:
        raise RuntimeError("ch178 not found in the channel list")
    listed_key = str(channel.get("key") or "")
    print(f"list payload: key_masked={'*' in listed_key} key_sha={sha(listed_key)}")

    target_models = models if NEW_MODEL in models else models + [NEW_MODEL]
    target_mapping = dict(mapping)
    target_mapping[NEW_MODEL] = UPSTREAM_MODEL

    payload = {k: v for k, v in channel.items() if k != "status"}
    payload["key"] = ssot_key  # hydrate from the SSOT: the list payload may be masked
    payload["models"] = ",".join(target_models)
    payload["model_mapping"] = json.dumps(target_mapping, ensure_ascii=False)
    payload["priority"] = TARGET_PRIORITY
    print(f"plan: models {len(models)} -> {len(target_models)} (+{NEW_MODEL}), "
          f"mapping += {{{NEW_MODEL}: {UPSTREAM_MODEL}}}, priority {priority} -> {TARGET_PRIORITY}")

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    dest = db_path.parent / "backups" / f"newapi-before-zen-primary-glm53flash-{time.strftime('%Y%m%d-%H%M%S')}.db"
    (db_path.parent / "backups").mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src, \
         closing(sqlite3.connect(dest, timeout=30)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("snapshot integrity failed")
    print(f"snapshot: {dest.name} ({dest.stat().st_size} bytes)")

    original_payload = {k: v for k, v in channel.items() if k != "status"}
    original_payload["key"] = ssot_key

    def put(body: dict) -> None:
        st, resp = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT",
                                   body=body, headers=headers)
        if st != 200 or not isinstance(resp, dict) or not resp.get("success"):
            raise RuntimeError(f"PUT failed: HTTP {st} {str(resp)[:160]}")

    try:
        put(payload)
        smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers)
        time.sleep(1)
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
            got = cn.execute(
                "SELECT key, models, coalesce(model_mapping,''), priority, coalesce(channel_info,'')"
                " FROM channels WHERE id=?", (CHANNEL_ID,)).fetchone()
            abilities = cn.execute(
                "SELECT COUNT(*), COALESCE(SUM(enabled),0) FROM abilities WHERE channel_id=? AND model=?",
                (CHANNEL_ID, NEW_MODEL)).fetchone()
        got_models = [m.strip() for m in (got[1] or "").split(",") if m.strip()]
        got_mapping = json.loads(got[2]) if got[2].strip() else {}
        print(f"readback: models={len(got_models)} has_new={NEW_MODEL in got_models} "
              f"mapping_ok={got_mapping.get(NEW_MODEL) == UPSTREAM_MODEL} priority={got[3]} "
              f"key_same={sha(got[0]) == sha(ssot_key)} "
              f"channel_info_same={got[4] == channel_info} abilities={abilities}")
        if sha(got[0]) != sha(ssot_key) or got[3] != TARGET_PRIORITY or NEW_MODEL not in got_models:
            raise RuntimeError("readback mismatch")
        if abilities[0] == 0 or abilities[1] == 0:
            raise RuntimeError(f"ability row missing/disabled for {NEW_MODEL}: {abilities}")

        st, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{CHANNEL_ID}?model={NEW_MODEL}",
            headers=headers, timeout=180)
        ok = isinstance(body, dict) and body.get("success")
        print(f"admin test ch{CHANNEL_ID}/{NEW_MODEL}: HTTP {st} success={ok}")

        import re
        omp_key = next(m.group(1) for m in (
            re.match(r"^\s*apiKey:\s*(sk-\S+)\s*$", line)
            for line in (Path.home() / ".omp" / "agent" / "models.yml").read_text(
                encoding="utf-8").splitlines()) if m)
        relay = json.dumps({"model": NEW_MODEL, "max_tokens": 64,
                            "messages": [{"role": "user", "content": "Reply with one word: OK"}]}).encode()
        req = urllib.request.Request(f"{smoke.NEWAPI_BASE}/v1/chat/completions", data=relay,
                                     headers={"Authorization": f"Bearer {omp_key}",
                                              "Content-Type": "application/json",
                                              "User-Agent": "Mozilla/5.0 Chrome/129"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            parsed = json.loads(resp.read().decode("utf-8", "replace"))
        text = ((parsed.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        print(f"relay {NEW_MODEL}: 200 content={str(text)[:20]!r}")
        time.sleep(2)
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
            last = cn.execute(
                "SELECT channel_id, created_at FROM logs WHERE model_name=? ORDER BY id DESC LIMIT 1",
                (NEW_MODEL,)).fetchone()
        print(f"log attribution: newest request served by channel_id={last[0] if last else None}"
              f" (expect {CHANNEL_ID})")
        if not last or int(last[0]) != CHANNEL_ID:
            raise RuntimeError("routing attribution is not ch178")
        print(f"OK: ch{CHANNEL_ID} is now the primary leg for {NEW_MODEL}; snapshot={dest.name}")
        return 0
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: {error}")
        try:
            put(original_payload)
            print("rollback: original ch178 payload restored")
        except Exception as rb:  # noqa: BLE001
            print(f"rollback FAILED: {rb}; snapshot kept at {dest.name}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
