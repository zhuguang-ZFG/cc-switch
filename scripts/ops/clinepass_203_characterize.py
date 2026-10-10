"""Characterize ch203 after the 5h reset + find why the gateway leg 401'd.

- pinned channel test across three subscription models (talks to ch203 only)
- gateway E2E with a token read from the local DB (keys stay in memory; only
  length + sha8 are ever printed)
- logs attribution check for channel_id=203
"""
import hashlib
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
MODELS = ["glm-5.3-flash", "deepseek-v4.1-flash", "kimi-k3"]


def call(path, payload=None, headers=None, timeout=180):
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


def main():
    sec = json.loads(SECRETS.read_text(encoding="utf-8"))
    admin_h = {"Authorization": f"Bearer {sec['newapi_token']}", "New-Api-User": str(sec.get("newapi_user") or 1)}
    pk = str(sec.get("newapi_probe_key") or "")
    print(f"probe_key fingerprint len={len(pk)} sha8={hashlib.sha256(pk.encode()).hexdigest()[:8]}", flush=True)

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    max_id = con.execute("SELECT COALESCE(MAX(id),0) FROM logs").fetchone()[0]
    # is the probe key even a row in tokens?
    ph = "k"
    rows = con.execute('SELECT id,user_id,name,key,length(key) AS l,status,"group" FROM tokens WHERE deleted_at IS NULL ORDER BY id LIMIT 12').fetchall()
    match = [r for r in rows if r["key"] == pk]
    print("probe_key_in_db =", bool(match), flush=True)
    usable = [r for r in rows if r["status"] == 1]
    tok = usable[0] if usable else None
    gateway_key = tok["key"] if tok else None
    print(f"using_token id={tok['id'] if tok else None} name={tok['name'] if tok else None} "
          f"len={tok['l'] if tok else None} group={tok['group'] if tok else None} "
          f"sha8={hashlib.sha256((tok['key'] or '').encode()).hexdigest()[:8] if tok else None}", flush=True)
    con.close()

    for m in MODELS:
        st, raw = call(f"/api/channel/test/{CH}?model={m}", headers=admin_h)
        print(f"PINNED {m} http={st} body={raw[:230].replace(chr(10),' ')}", flush=True)
        time.sleep(2)

    if gateway_key:
        body = {"model": "glm-5.3",
                "messages": [{"role": "system", "content": "You are a helpful assistant."},
                             {"role": "user", "content": "Reply with exactly CLINEPASS203_OK"}],
                "max_tokens": 32}
        for i in range(3):
            st, raw = call("/v1/chat/completions", payload=body,
                           headers={"Authorization": f"Bearer {gateway_key}"})
            con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            hits = con.execute("SELECT id,type,model_name,channel_id,channel_name,quota,prompt_tokens,"
                               "completion_tokens,use_time,is_stream,content FROM logs WHERE id>?"
                               " AND (channel_id=? OR type=2) ORDER BY id DESC LIMIT 5", (max_id, CH)).fetchall()
            con.close()
            print(f"E2E try={i+1} http={st} head={raw[:180].replace(chr(10),' ')}", flush=True)
            for h in hits:
                print("   LOG " + json.dumps(dict(h), ensure_ascii=False, default=str)[:260], flush=True)
            if any(h["channel_id"] == CH and h["type"] == 2 for h in hits):
                break
            time.sleep(3)


if __name__ == "__main__":
    main()
