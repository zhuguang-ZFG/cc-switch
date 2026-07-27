#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_optimizer.py v3 — TTFT + error/EOF-rate adaptive weights for NewAPI.

v2 over v1:
- streaming TTFT probe (first-byte latency, aborts early = cheaper)
- quality signal: per-channel error rate parsed from container logs
  (channel error lines incl. mid-stream EOF handler_stop), EWMA-smoothed
- TG alerts on dead/recovery/all-dead transitions (via /opt/new-api/tg_notify.py)

v3 (2026-07-27):
- __main__ 追加调用 route_optimizer_sonnet.sonnet_main()（Sonnet 兜底链层间
  自动换序，同 cron 串行）；--restore 不再连带触发 sonnet 周期
- 入口 flock（route_optimizer.lock）防 cron 重叠

Only adjusts `weight` within the top-priority tier; never touches
priority/status/models. Design: docs/plans/newapi-adaptive-routing-2026-07-27.md
"""
import json, os, re, sqlite3, subprocess, sys, time, urllib.request

sys.path.insert(0, "/opt/new-api")
try:
    from tg_notify import send_tg
except Exception:
    def send_tg(text, **kw):
        return False

DB = "/opt/new-api/data/one-api.db"
STATE = "/opt/new-api/data/route_optimizer_state.json"
LOG = "/opt/new-api/data/route_optimizer.log"

MODEL_FAMILY = "claude-opus%"
PROBE_MODEL = "claude-opus-5"
EXCLUDE_CHANNELS = {11}
PROBE_TIMEOUT = 30
LOG_WINDOW = "6m"                       # container-log slice per run
TRAFFIC_WINDOW_S = 360                  # success count window (matches cron)
EWMA_ALPHA = 0.4
ERR_PENALTY = 3.0                       # quality = 1/(1 + ERR_PENALTY*err_rate)
W_MIN_OK, W_MAX, W_DEAD = 3, 60, 1
HYSTERESIS = 5

ERR_RE = re.compile(r"channel error \(channel #(\d+)")


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-1000:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except Exception:
        pass
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ewma_ttft": {}, "ewma_err": {}, "dead": [], "backup_weights": None}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def probe_ttft(base_url, key, model=None):
    """Streaming TTFT seconds, or None on failure. Aborts after first SSE chunk."""
    body = json.dumps({"model": model or PROBE_MODEL, "max_tokens": 8, "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01",
                 "user-agent": "claude-cli/2.1.2 (external, cli)"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            if not (200 <= r.status < 300):
                return None
            for chunk in r:
                if chunk.strip():
                    return time.time() - t0
            return None
    except Exception:
        return None


def channel_errors(ids):
    """Per-channel error count from the last LOG_WINDOW of container logs."""
    errs = {cid: 0 for cid in ids}
    try:
        out = subprocess.run(
            ["podman", "logs", "--since", LOG_WINDOW, "new-api"],
            capture_output=True, text=True, timeout=60).stdout
        for m in ERR_RE.finditer(out):
            cid = int(m.group(1))
            if cid in errs:
                errs[cid] += 1
    except Exception as e:
        log("WARN container-log parse failed: %s" % e)
    return errs


def main():
    restore = "--restore" in sys.argv
    st = load_state()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

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
            db.execute("UPDATE abilities SET weight=? WHERE channel_id=? AND model LIKE ?",
                       (w, int(cid), MODEL_FAMILY))
        db.commit()
        log("restored weights: %s" % bw)
        return

    if st.get("backup_weights") is None:
        st["backup_weights"] = {str(r["id"]): r["weight"] for r in tier}
        save_state(st)

    # successes from real traffic (short window; logs only record successes)
    succ = {cid: 0 for cid in ids}
    q = ("SELECT channel_id, COUNT(*) n FROM logs WHERE model_name LIKE ?"
         " AND created_at > ? AND channel_id IN (%s) GROUP BY channel_id"
         % ",".join("?" * len(ids)))
    for r in db.execute(q, [MODEL_FAMILY, int(time.time()) - TRAFFIC_WINDOW_S] + ids):
        succ[r["channel_id"]] = r["n"]

    errs = channel_errors(ids)

    scores = {}
    for r in tier:
        cid = r["id"]
        name = r["name"][:26]
        ttft = probe_ttft(r["base_url"], r["key"])
        if ttft is None:
            scores[cid] = 0.0
            log("probe FAIL ch#%d %s (errs=%d succ=%d)" % (cid, name, errs[cid], succ[cid]))
            continue
        prev_t = st.setdefault("ewma_ttft", {}).get(str(cid), ttft)
        ew_t = EWMA_ALPHA * ttft + (1 - EWMA_ALPHA) * prev_t
        st["ewma_ttft"][str(cid)] = ew_t
        rate = errs[cid] / (errs[cid] + succ[cid] + 1.0)
        prev_r = st.setdefault("ewma_err", {}).get(str(cid), rate)
        ew_r = EWMA_ALPHA * rate + (1 - EWMA_ALPHA) * prev_r
        st["ewma_err"][str(cid)] = ew_r
        lat = 0.5 * ew_t + 0.5 * ttft
        quality = 1.0 / (1.0 + ERR_PENALTY * ew_r)
        scores[cid] = quality / max(lat, 0.3)
        log("ch#%-3d %-26s ttft=%5.1fs lat=%5.1fs err=%.2f q=%.2f (errs=%d succ=%d)" % (
            cid, name, ttft, lat, ew_r, quality, errs[cid], succ[cid]))

    total = sum(scores.values())
    new_w = {}
    for r in tier:
        cid = r["id"]
        new_w[cid] = W_DEAD if (scores[cid] <= 0 or total <= 0) else \
            max(W_MIN_OK, min(W_MAX, round(100 * scores[cid] / total)))

    cur_w = {r["id"]: r["weight"] for r in tier}
    if any(abs(new_w[c] - cur_w[c]) >= HYSTERESIS for c in new_w):
        for cid, w in new_w.items():
            db.execute("UPDATE channels SET weight=? WHERE id=?", (w, cid))
            db.execute("UPDATE abilities SET weight=? WHERE channel_id=? AND model LIKE ?",
                       (w, cid, MODEL_FAMILY))
        db.commit()
        log("APPLY weights: %s" % new_w)
    else:
        log("no change (hysteresis), weights: %s" % new_w)

    # TG alerts on dead-set transitions
    names = {r["id"]: r["name"] for r in tier}
    dead = sorted(c for c, s in scores.items() if s <= 0)
    prev_dead = st.get("dead", [])
    newly = [c for c in dead if c not in prev_dead]
    healed = [c for c in prev_dead if c not in dead]
    if dead and len(dead) == len(tier):
        send_tg("🚨 *Opus 主池全灭*：所有受管渠道探针失败\nweights: %s" % new_w)
    else:
        for c in newly:
            send_tg("⚠️ Opus 渠道判死：#%d %s → w1" % (c, names.get(c, "")))
        for c in healed:
            send_tg("✅ Opus 渠道恢复：#%d %s → w%d" % (c, names.get(c, ""), new_w[c]))
    if newly or healed:
        log("dead-set: %s (newly=%s healed=%s)" % (dead, newly, healed))
    st["dead"] = dead

    save_state(st)
    db.close()


LOCK = "/opt/new-api/data/route_optimizer.lock"


def _acquire_lock():
    """cron 重叠保护：拿不到锁直接退出（上一轮还在跑）。"""
    import fcntl
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another optimizer run holds the lock, exiting")
        sys.exit(0)
    return fd  # 保持 fd 打开至进程结束，锁随进程释放


if __name__ == "__main__":
    _lock_fd = _acquire_lock()
    if "--restore-sonnet" in sys.argv or "--sonnet-dry" in sys.argv:
        import route_optimizer_sonnet
        route_optimizer_sonnet.sonnet_main()
    else:
        main()
        if "--restore" not in sys.argv:
            try:
                import route_optimizer_sonnet
                route_optimizer_sonnet.sonnet_main()
            except Exception as e:
                log("sonnet reorder error: %s" % e)
