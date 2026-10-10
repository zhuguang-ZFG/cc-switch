"""Post-window-reset verification for ch203 (clinepass). Read-only + bounded probes.

Leg 1: pinned channel test  GET /api/channel/test/203?model=glm-5.3-flash
       -- deterministic, talks to ch203 only.
Leg 2: gateway E2E          POST /v1/chat/completions model=glm-5.3
       -- proves pool short name -> cline-pass/glm-5.3 mapping, confirmed by the
       logs row's channel_id. Bounded attempts, stops on first 203 attribution.

Never prints any key/token: secrets are read and used in-memory only.
"""
import json
import pathlib
import sqlite3
import time
import urllib.error
import urllib.request

SECRETS = pathlib.Path.home() / ".omp" / "guardian" / "secrets.json"
BASE = "http://127.0.0.1:3002"
DB = pathlib.Path.home() / ".new-api-local" / "new-api.db"
CH = 203
PINNED_MODEL = "glm-5.3-flash"
POOL_MODEL = "glm-5.3"
MAX_E2E_ATTEMPTS = 4


def call(path, payload=None, headers=None, timeout=120):
    req = urllib.request.Request(BASE + path, method="POST" if payload is not None else "GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def summarize(kind, status, raw):
    out = {"kind": kind, "http": status, "sec_ago_head": raw[:400].replace("\n", " ")}
    try:
        d = json.loads(raw)
    except Exception:
        return out
    if isinstance(d, dict) and "data" in d and isinstance(d["data"], (dict, str)):
        inner = d["data"]
        out["admin_ok"] = d.get("success")
        out["admin_message"] = str(d.get("message"))[:120]
        if isinstance(inner, dict):
            out.update({k: inner[k] for k in ("time", "model", "testing", "error") if k in inner})
        else:
            out["data"] = str(inner)[:200]
    ch = (d.get("choices") or [{}])[0] if isinstance(d, dict) else {}
    if ch:
        out["text"] = ((ch.get("message") or {}).get("content") or "")[:60] or None
        out["finish"] = ch.get("finish_reason")
        out["usage"] = d.get("usage")
    if isinstance(d, dict) and d.get("error"):
        out["error"] = str(d["error"])[:200]
    return out


def latest_203(after_id):
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, created_at, type, model_name, channel_id, channel_name, quota,"
        " prompt_tokens, completion_tokens, use_time, is_stream, content"
        " FROM logs WHERE id > ? ORDER BY id DESC LIMIT 12", (after_id,)).fetchall()
    con.close()
    return rows


def main():
    sec = json.loads(SECRETS.read_text(encoding="utf-8"))
    admin = sec["newapi_token"]
    user = str(sec.get("newapi_user") or 1)
    probe_key = sec["newapi_probe_key"]
    admin_h = {"Authorization": f"Bearer {admin}", "New-Api-User": user}

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    max_id = con.execute("SELECT COALESCE(MAX(id),0) FROM logs").fetchone()[0]
    ch_row = con.execute("SELECT id, status, name, test_model FROM channels WHERE id=?", (CH,)).fetchone()
    con.close()
    print("channel_before =", dict(ch_row), "logs_max_id =", max_id, flush=True)

    st, raw = call(f"/api/channel/test/{CH}?model={PINNED_MODEL}", headers=admin_h, timeout=180)
    print("LEG1 " + json.dumps(summarize("pinned channel test", st, raw), ensure_ascii=False), flush=True)

    attrib = None
    for i in range(MAX_E2E_ATTEMPTS):
        body = {"model": POOL_MODEL,
                "messages": [{"role": "system", "content": "You are a helpful assistant."},
                             {"role": "user", "content": "Reply with exactly CLINEPASS203_OK"}],
                "max_tokens": 32}
        st, raw = call("/v1/chat/completions", payload=body,
                       headers={"Authorization": f"Bearer {probe_key}"}, timeout=180)
        rows = latest_203(max_id)
        hit = [r for r in rows if r["channel_id"] == CH]
        others = [(r["channel_id"], r["model_name"]) for r in rows if r["type"] == 2]
        print(f"LEG2 try={i+1} " + json.dumps(summarize("gateway e2e", st, raw), ensure_ascii=False), flush=True)
        print(f"LEG2 new_log_rows={len(rows)} attributed_203={len(hit)} conv_rows={others}", flush=True)
        if hit:
            r = hit[0]
            attrib = dict(r)
            break
        time.sleep(3)

    print("ATTRIBUTION " + json.dumps(attrib, ensure_ascii=False, default=str), flush=True)
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    after = con.execute("SELECT id, status, test_model, other_info FROM channels WHERE id=?", (CH,)).fetchone()
    con.close()
    print("channel_after =", dict(after), flush=True)


if __name__ == "__main__":
    main()
