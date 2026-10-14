#!/usr/bin/env python3
"""Trim ch173 muyuan-gongyi to the model set muyuan.do still distributes (2026-10-14).

muyuan.do shrank its catalog for our token group from 31 ids (2026-10-13) to 7
(5 chat survivors + jev-latest/jev-preview). Direct probes on both channel keys:
- survivors 200 on both keys (glm-4.5-flash, deepseek-v4.1-flash, glm-5.3-flash,
  grok-4.7, qwen3.8-27b); jev ids 400 invalid_request on all payload shapes
  (no chat passthrough for our group -> excluded; official jev stays on ch181).
- every removed id: 503 model_not_found under group default (mistral/ministral/
  codestral/voxtral family, qwen3.8-flash-next, qwen3-8-27b legacy form, glm-5.2).

Applying:
- ch173 PUT models -> TARGET (503-distributing ids removed; obsolete qwen3-8-27b
  model_mapping dropped; test_model ministral-3b-latest -> glm-4.5-flash).
- abilities rebuild (POST fix), cache-window wait, per-model posture assert
  (restore to pre-change snapshot if the fork PUT quirk drifts them).
- read-back + admin channel test x5 + gateway relay attribution.

Key never printed: read from DB channels.key at runtime. Backup before write.
Stale ModelRatio/CompletionRatio option rows for removed ids are left in place
(dead ids, benign; other-option cleanup out of scope).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
CHANNEL_ID = 173
CHANNEL_NAME = "muyuan-gongyi"
BASE_URL = "https://muyuan.do"
TEST_MODEL = "glm-4.5-flash"
PRIORITY = 20
WEIGHT = 1
AUTO_BAN = 1
TARGET = sorted([
    "deepseek-v4.1-flash",
    "glm-4.5-flash",
    "glm-5.3-flash",
    "grok-4.7",
])
# ids dropped from the channel this run (upstream 503 model_not_found under our group,
# or — qwen3.8-27b — catalog-listed but chat 504 x5 over 1h through Cloudflare)
REMOVED = sorted([
    "codestral-2508", "codestral-embed", "codestral-embed-2505", "codestral-latest",
    "ministral-14b-2512", "ministral-14b-latest", "ministral-3b-2512",
    "ministral-3b-latest", "ministral-8b-2512", "ministral-8b-latest",
    "mistral-code-fim-latest", "mistral-code-latest", "mistral-embed",
    "mistral-embed-2312", "mistral-medium-latest", "mistral-small-latest",
    "qwen3-8-27b", "qwen3.8-27b", "qwen3.8-flash-next", "voxtral-small-2507",
    "voxtral-small-latest", "glm-5.2",
])


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_rows(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def online_backup(db_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = db_path.parent / "backups" / f"new-api-before-muyuan-sync-{stamp}.db"
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(destination.as_posix())) as dst:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"backup integrity_check={integrity}")
    return destination


def abilities_posture() -> dict[str, tuple]:
    rows = db_rows(
        "SELECT model, \"group\", enabled, priority, weight FROM abilities WHERE channel_id = ?",
        (CHANNEL_ID,),
    )
    return {m: (g, e, p, w) for m, g, e, p, w in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    parser.add_argument("--relay-only", action="store_true", help="skip PUT/tests; only run gateway relay probes")
    parser.add_argument("--skip-relay", action="store_true", help="skip gateway relay probes")
    args = parser.parse_args()

    pre = abilities_posture()
    current = sorted(m for m in pre)
    print(f"pre-change abilities ch{CHANNEL_ID}: {len(current)} models")
    drift_targets = {m: pre.get(m) for m in TARGET}
    print(f"target models: {TARGET}")
    print(f"removing {len(REMOVED)} ids: {REMOVED}")

    if not args.apply and not args.relay_only:
        print("dry-run: no changes made")
        return 0

    smoke = load_smoke()
    if args.relay_only:
        args.skip_relay = False
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    rows = db_rows("SELECT key, type FROM channels WHERE id = ?", (CHANNEL_ID,))
    if not rows:
        raise RuntimeError(f"ch{CHANNEL_ID} missing")
    key_blob, ch_type = rows[0]
    key_lines = [l.strip() for l in key_blob.split("\n") if l.strip()]
    if len(key_lines) != 2:
        raise RuntimeError(f"expected 2-key blob, got {len(key_lines)} lines")
    if ch_type != 1:
        raise RuntimeError(f"unexpected channel type {ch_type}")

    if args.relay_only:
        backup = None
    else:
        backup = online_backup(DB_PATH)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    payload = {
        "id": CHANNEL_ID,
        "name": CHANNEL_NAME,
        "type": 1,
        "base_url": BASE_URL,
        "key": key_blob,
        "models": ",".join(TARGET),
        "model_mapping": json.dumps({}, ensure_ascii=False),
        "group": "default",
        "priority": PRIORITY,
        "weight": WEIGHT,
        "auto_ban": AUTO_BAN,
        "test_model": TEST_MODEL,
    }
    if args.relay_only:
        print("relay-only: skipping PUT/fix/read-back/admin tests")
    else:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=payload, headers=headers
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"channel PUT failed: HTTP {status} message={message!r}")
        print(f"PUT ok: models -> {len(TARGET)}")

        # fix rebuilds abilities; fork cache window then per-model posture check
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(f"channel fix failed: HTTP {status}")

        deadline = time.monotonic() + 150
        post = {}
        while time.monotonic() < deadline:
            post = abilities_posture()
            if all(m in post for m in TARGET):
                break
            time.sleep(5)
        restored = []
        for m in TARGET:
            want = drift_targets.get(m) or ("default", 1, PRIORITY, WEIGHT)
            if post.get(m) != want:
                with closing(sqlite3.connect(DB_PATH.as_posix(), timeout=30)) as c:
                    c.execute(
                        "UPDATE abilities SET \"group\"=?, enabled=?, priority=?, weight=? "
                        "WHERE channel_id=? AND model=?",
                        (*want, CHANNEL_ID, m),
                    )
                    c.commit()
                restored.append(f"{m}={post.get(m)}->{want}")
        if restored:
            print(f"posture restored after PUT quirk: {restored}")
        else:
            print("posture ok: all target rows unchanged by PUT")

        leftover = [m for m in REMOVED if m in abilities_posture()]
        if leftover:
            raise RuntimeError(f"removed ids still in abilities: {leftover}")

        # read-back: stored model set must equal TARGET exactly
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=500", headers=headers
        )
        items = (body.get("data") or {}).get("items") or []
        stored = next(c for c in items if int(c.get("id") or 0) == CHANNEL_ID)
        got = sorted(x for x in str(stored.get("models") or "").split(",") if x)
        if got != TARGET:
            raise RuntimeError(f"read-back mismatch: delta={sorted(set(TARGET) ^ set(got))}")
        mapping = json.loads(str(stored.get("model_mapping") or "{}"))
        print(f"read-back ok: ch{CHANNEL_ID} models={len(got)} mapping={mapping}")

        # multi-key manifest preserved through PUT
        ci_raw = db_rows("SELECT channel_info FROM channels WHERE id = ?", (CHANNEL_ID,))[0][0]
        ci = ci_raw if isinstance(ci_raw, dict) else json.loads(bytes(ci_raw).decode("utf-8"))
        if not (ci.get("is_multi_key") and int(ci.get("multi_key_size") or 0) == 2):
            raise RuntimeError(f"channel_info regressed: {ci}")
        print(f"channel_info ok: is_multi_key={ci['is_multi_key']} size={ci['multi_key_size']}")

        # admin channel test x5 (single snapshot gate: all 200)
        for m in TARGET:
            s, b = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/test/{CHANNEL_ID}?model={m}",
                headers=headers, timeout=280,
            )
            ok = isinstance(b, dict) and b.get("success")
            if not ok and s == 200 and isinstance(b, dict) and "504" in str(b.get("message", "")):
                time.sleep(5)
                s, b = smoke.http_json(
                    f"{smoke.NEWAPI_BASE}/api/channel/test/{CHANNEL_ID}?model={m}",
                    headers=headers, timeout=280,
                )
                ok = isinstance(b, dict) and b.get("success")
            print(f"admin test {m}: HTTP {s} success={ok}")
            if not ok:
                raise RuntimeError(f"admin test failed for {m}: {str(b)[:160]}")

    if args.skip_relay:
        print("relay probes skipped (--skip-relay)")
    else:
        import re as _re
        yml = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
        block = _re.search(r"\n  zg-newapi:\n(.*?)(?=\n  [a-zA-Z][\w.-]*:\n)", yml, _re.S)
        gkey = _re.search(r"apiKey:\s*(\S+)", block.group(1)).group(1)
        for m in ("glm-5.3-flash", "glm-4.5-flash"):
            mark = time.time()
            s, b = smoke.http_json(
                "http://127.0.0.1:3002/v1/chat/completions", method="POST",
                body={"model": m, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                      "max_tokens": 32},
                headers={"Authorization": f"Bearer {gkey}"}, timeout=180,
            )
            if s != 200:
                raise RuntimeError(f"relay {m} failed: HTTP {s}")
            attr = None
            for _ in range(8):
                rows = db_rows(
                    "SELECT channel_id FROM logs WHERE model_name=? AND created_at>? AND type=2 "
                    "ORDER BY id DESC LIMIT 1", (m, mark))
                if rows:
                    attr = int(rows[0][0]); break
                time.sleep(5)
            print(f"relay {m}: 200 attributed ch{attr}")
            if attr != CHANNEL_ID:
                print(f"warning: {m} attributed ch{attr} (not ch{CHANNEL_ID}) -- check pool priorities")

    print(f"DONE: ch{CHANNEL_ID} {CHANNEL_NAME} trimmed to {len(TARGET)} models; removed {len(REMOVED)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
