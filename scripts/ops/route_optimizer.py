#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_optimizer.py — latency+quality adaptive weights for NewAPI same-tier channels.

Runs on the VPS as a cron job (every 5 min). Only adjusts `weight` within the
top-priority tier of a model family; never touches priority/status/models.
Design: docs/plans/newapi-adaptive-routing-2026-07-27.md
"""
import json, os, sqlite3, sys, time, urllib.request

DB = "/opt/new-api/data/one-api.db"
STATE = "/opt/new-api/data/route_optimizer_state.json"
LOG = "/opt/new-api/data/route_optimizer.log"

MODEL_FAMILY = "claude-opus%"          # v1: Opus pool only
PROBE_MODEL = "claude-opus-5"
EXCLUDE_CHANNELS = {11}                # manual conservative trickle; optimizer must not touch
PROBE_TIMEOUT = 30
TRAFFIC_WINDOW_S = 1800                 # 30 min real-traffic window
EWMA_ALPHA = 0.4
W_MIN_OK, W_MAX, W_DEAD = 3, 60, 1
HYSTERESIS = 5


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ewma": {}, "backup_weights": None}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def probe(base_url, key):
    """Return latency seconds or None on failure."""
    body = json.dumps({"model": PROBE_MODEL, "max_tokens": 8,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01",
                 "user-agent": "claude-cli/2.1.2 (external, cli)"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            r.read()
            return time.time() - t0 if 200 <= r.status < 300 else None
    except Exception:
        return None


def main():
    restore = "--restore" in sys.argv
    st = load_state()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    # top tier = highest priority among live channels serving the family
    rows = db.execute(
        "SELECT DISTINCT c.id, c.name, c.base_url, c.key, c.weight, c.priority"
        " FROM channels c JOIN abilities a ON a.channel_id = c.id"
        " WHERE c.status = 1 AND a.enabled = 1 AND a.model LIKE ?"
        " ORDER BY c.priority DESC", (MODEL_FAMILY,)).fetchall()
    if not rows:
        log("no live channels for %s" % MODEL_FAMILY)
        return
    top_pri = rows[0]["priority"]
    tier = [r for r in rows if r["priority"] == top_pri and r["id"] not in EXCLUDE_CHANNELS]
    ids = [r["id"] for r in tier]

    if restore:
        bw = st.get("backup_weights") or {}
        for cid, w in bw.items():
            db.execute("UPDATE channels SET weight=? WHERE id=?", (w, int(cid)))
            db.execute("UPDATE abilities SET weight=? WHERE channel_id=?", (w, int(cid)))
        db.commit()
        log("restored weights: %s" % bw)
        return

    if st.get("backup_weights") is None:
        st["backup_weights"] = {str(r["id"]): r["weight"] for r in tier}
        save_state(st)

    # real-traffic latency (30 min window)
    traffic = {}
    q = ("SELECT channel_id, AVG(use_time) av, COUNT(*) n FROM logs"
         " WHERE model_name LIKE ? AND created_at > ? AND channel_id IN (%s)"
         " GROUP BY channel_id" % ",".join("?" * len(ids)))
    for r in db.execute(q, [MODEL_FAMILY, int(time.time()) - TRAFFIC_WINDOW_S] + ids):
        traffic[r["channel_id"]] = (r["av"], r["n"])

    # score each channel
    scores = {}
    for r in tier:
        cid = r["id"]
        p = probe(r["base_url"], r["key"])
        if p is None:
            scores[cid] = 0.0
            log("probe FAIL ch#%d %s" % (cid, r["name"]))
            continue
        av, n = traffic.get(cid, (None, 0))
        base = av if (av and n >= 3) else p
        prev = st["ewma"].get(str(cid), base)
        ew = EWMA_ALPHA * base + (1 - EWMA_ALPHA) * prev
        st["ewma"][str(cid)] = ew
        lat = 0.6 * ew + 0.4 * p
        scores[cid] = 1.0 / max(lat, 0.5)
        log("ch#%-3d %-26s probe=%5.1fs traffic_n=%-3d lat=%5.1fs" % (
            cid, r["name"][:26], p, n, lat))

    total = sum(scores.values())
    new_w = {}
    for r in tier:
        cid = r["id"]
        if scores[cid] <= 0 or total <= 0:
            new_w[cid] = W_DEAD
        else:
            new_w[cid] = max(W_MIN_OK, min(W_MAX, round(100 * scores[cid] / total)))

    changed = {cid: w for cid, w in new_w.items()
               if abs(w - dict((r["id"], r["weight"]) for r in tier)[cid]) >= HYSTERESIS}
    if changed:
        for cid, w in new_w.items():
            db.execute("UPDATE channels SET weight=? WHERE id=?", (w, cid))
            db.execute("UPDATE abilities SET weight=? WHERE channel_id=?", (w, cid))
        db.commit()
        log("APPLY weights: %s (takes effect within 60s via fork db-sync)" % new_w)
    else:
        log("no change (hysteresis), weights: %s" % new_w)
    save_state(st)
    db.close()


if __name__ == "__main__":
    main()
