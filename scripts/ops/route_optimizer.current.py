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

v4 (2026-07-27):
- 主池放映射渠（#137 GPT-terra / #138 kimi-k3，priority 45）：探针按渠道
  type 分流 OpenAI/Anthropic 格式，模型取 model_mapping，UA 吃 header_override
- 映射渠 margin 门控 + 权重封顶：分数须超最好 Claude 渠 ×MAPPED_MARGIN 才
  放权重（否则 w0 关门），且封顶 MAPPED_CAP；Claude 全灭时门控失效全开兜底

v5 (2026-07-27):
- 错误分级惩罚（借 CRS 分级冷却思路）：429/并发限制 ×0.5 轻罚（瞬时现象），
  5xx/连接故障 ×2.0，401/403 auth ×3.0；主池打分用 channel_error_classes，
  sonnet 仍用 channel_errors 总数（不换打分语义）

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
EXCLUDE_CHANNELS = set()
PROBE_TIMEOUT = 30
LOG_WINDOW = "6m"                       # container-log slice per run
TRAFFIC_WINDOW_S = 360                  # success count window (matches cron)
EWMA_ALPHA = 0.4
ERR_PENALTY = 3.0                       # quality = 1/(1 + ERR_PENALTY*err_rate)
W_MIN_OK, W_MAX, W_DEAD = 3, 60, 1
HYSTERESIS = 5

# 主池映射渠（非真 Claude）：margin 门控 + 权重封顶（泄压阀，不当主力）
MAIN_TIER_MAPPED = {137, 138, 139}
MAPPED_MARGIN = 1.3                     # 分数须超最好 Claude 渠 ×1.3 才放权重
MAPPED_CAP = 25
TYPE_ANTHROPIC = 14

ERR_RE = re.compile(r"channel error \(channel #(\d+)(?:, status code: (\d+))?\): ([^\n]*)")

# 错误分级权重（v5, 2026-07-27）：429/并发限制是瞬时现象轻罚（0.5），
# 5xx/连接故障中罚（2.0），401/403 auth 类重罚（3.0）。参考 CRS 分级冷却模型。
# v5.2：sensitive_words 等内容拒绝不是渠道故障，单列一类权重 0（只统计不惩罚）。
ERR_W_AUTH, ERR_W_CONC, ERR_W_OTHER, ERR_W_CONTENT = 3.0, 0.5, 2.0, 0.0


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


def probe_openai_ttft(base_url, key, model, ua="new-api-route-optimizer/3.0"):
    """OpenAI /chat/completions 流式 TTFT 秒数，失败返回 None。"""
    body = json.dumps({"model": model, "max_tokens": 8, "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions", data=body,
        headers={"content-type": "application/json",
                 "authorization": "Bearer " + key, "user-agent": ua})
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


def probe_responses_ttft(base_url, key, model, ua):
    """OpenAI Responses /v1/responses 流式 TTFT（welfare 站只收此协议）。"""
    body = json.dumps({"model": model, "max_output_tokens": 8, "stream": True,
                       "input": "hi"}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/responses", data=body,
        headers={"content-type": "application/json",
                 "authorization": "Bearer " + key, "user-agent": ua})
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


# 只收 OpenAI Responses 协议的渠道（如 #142 welfare.0xpsyche.me）
RESPONSES_CHANNELS = {142}


def _mapped_model(row, req_model):
    try:
        mm = json.loads(row["model_mapping"] or "{}")
    except Exception:
        mm = {}
    return mm.get(req_model, req_model)


def _hdr_ua(row):
    try:
        h = json.loads(row["header_override"] or "{}")
    except Exception:
        h = {}
    return h.get("User-Agent") or "new-api-route-optimizer/3.0"


def _probe_channel(r):
    """按渠道 type 分流探针格式；模型取 model_mapping 映射值；多 key 取首 key。"""
    key = (r["key"] or "").split("\n")[0].strip()
    model = _mapped_model(r, PROBE_MODEL)
    if r["type"] == TYPE_ANTHROPIC:
        return probe_ttft(r["base_url"], key, model)
    if r["id"] in RESPONSES_CHANNELS:
        return probe_responses_ttft(r["base_url"], key, model, _hdr_ua(r))
    return probe_openai_ttft(r["base_url"], key, model, _hdr_ua(r))


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


def channel_error_classes(ids):
    """Per-channel classified error counts: auth / conc / other / content."""
    cls = {cid: {"auth": 0, "conc": 0, "other": 0, "content": 0} for cid in ids}
    try:
        out = subprocess.run(
            ["podman", "logs", "--since", LOG_WINDOW, "new-api"],
            capture_output=True, text=True, timeout=60).stdout
        for m in ERR_RE.finditer(out):
            cid = int(m.group(1))
            if cid not in cls:
                continue
            status = int(m.group(2) or 0)
            msg = (m.group(3) or "").lower()
            if "sensitive_words" in msg:
                cls[cid]["content"] += 1
            elif status in (401, 403):
                cls[cid]["auth"] += 1
            elif status == 429 or "concurrency limit" in msg:
                cls[cid]["conc"] += 1
            else:
                cls[cid]["other"] += 1
    except Exception as e:
        log("WARN container-log parse failed: %s" % e)
    return cls


def weighted_err_count(c):
    return (ERR_W_AUTH * c["auth"] + ERR_W_CONC * c["conc"]
            + ERR_W_OTHER * c["other"] + ERR_W_CONTENT * c["content"])


def channel_cache_rates(db, ids):
    """Recent cache hit rate per channel from logs. Returns {cid: pct}."""
    rates = {cid: None for cid in ids}
    try:
        q = ("SELECT channel_id,"
             " SUM(CAST(json_extract(other,'$.cache_tokens') AS INTEGER)) AS ct,"
             " SUM(prompt_tokens) AS pt"
             " FROM logs WHERE model_name LIKE ? AND created_at > ?"
             " AND channel_id IN (%s)"
             " GROUP BY channel_id" % ",".join("?" * len(ids)))
        cutoff = int(time.time()) - 3600
        for r in db.execute(q, [MODEL_FAMILY, cutoff] + ids):
            pt = r["pt"] or 0
            rates[r["channel_id"]] = (r["ct"] / pt * 100.0) if pt > 0 else 0.0
    except Exception as e:
        log("WARN cache-rate query failed: %s" % e)
    return rates


def main():
    restore = "--restore" in sys.argv
    st = load_state()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    rows = db.execute(
        "SELECT DISTINCT c.id, c.name, c.base_url, c.key, c.weight, c.priority,"
        " c.type, c.model_mapping, c.header_override"
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

    err_cls = channel_error_classes(ids)
    errs = {cid: sum(c.values()) for cid, c in err_cls.items()}
    cache_rates = channel_cache_rates(db, ids)

    scores = {}
    for r in tier:
        cid = r["id"]
        name = r["name"][:26]
        ttft = _probe_channel(r)
        if ttft is None:
            scores[cid] = 0.0
            log("probe FAIL ch#%d %s (errs=%d succ=%d)" % (cid, name, errs[cid], succ[cid]))
            continue
        prev_t = st.setdefault("ewma_ttft", {}).get(str(cid), ttft)
        ew_t = EWMA_ALPHA * ttft + (1 - EWMA_ALPHA) * prev_t
        st["ewma_ttft"][str(cid)] = ew_t
        werr = weighted_err_count(err_cls[cid])
        rate = werr / (errs[cid] + succ[cid] + 1.0)
        prev_r = st.setdefault("ewma_err", {}).get(str(cid), rate)
        ew_r = EWMA_ALPHA * rate + (1 - EWMA_ALPHA) * prev_r
        st["ewma_err"][str(cid)] = ew_r
        lat = 0.5 * ew_t + 0.5 * ttft
        quality = 1.0 / (1.0 + ERR_PENALTY * ew_r)
        scores[cid] = quality / max(lat, 0.3)
        c = err_cls[cid]
        cls_note = (" a%d/c%d/o%d/t%d" % (c["auth"], c["conc"], c["other"], c["content"])) if errs[cid] else ""
        cr = cache_rates.get(cid)
        cache_note = " cache=%.0f%%" % cr if cr is not None else ""
        log("ch#%-3d %-26s ttft=%5.1fs lat=%5.1fs err=%.2f q=%.2f%s (errs=%d%s succ=%d)" % (
            cid, name, ttft, lat, ew_r, quality, cache_note, errs[cid], cls_note, succ[cid]))

    total = sum(scores.values())
    new_w = {}
    for r in tier:
        cid = r["id"]
        new_w[cid] = W_DEAD if (scores[cid] <= 0 or total <= 0) else \
            max(W_MIN_OK, min(W_MAX, round(100 * scores[cid] / total)))

    # 映射渠门控：活着但分数未超最好 Claude 渠 × MAPPED_MARGIN → w0 关门；
    # 超过则权重封顶 MAPPED_CAP；Claude 全灭时门控失效（兜底服务全开）
    alive_claude = [scores[c] for c in ids
                    if c not in MAIN_TIER_MAPPED and scores[c] > 0]
    best_claude = max(alive_claude) if alive_claude else 0.0
    for c in ids:
        if c not in MAIN_TIER_MAPPED or scores[c] <= 0:
            continue
        if best_claude > 0 and scores[c] < best_claude * MAPPED_MARGIN:
            new_w[c] = 0
        else:
            new_w[c] = min(MAPPED_CAP, new_w[c])
    mapped_active = sorted(c for c in ids
                           if c in MAIN_TIER_MAPPED and new_w[c] > W_DEAD)
    prev_active = st.get("mapped_active", [])
    if mapped_active != prev_active and st.get("mapped_active") is not None:
        send_tg("🎚 主池映射渠门控变化：active=%s（cap=%d margin=%.1f）"
                % (mapped_active, MAPPED_CAP, MAPPED_MARGIN))
    st["mapped_active"] = mapped_active

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

    # TG alerts on dead-set transitions（映射渠 flap 不发判死/恢复告警，门控已覆盖）
    names = {r["id"]: r["name"] for r in tier}
    dead = sorted(c for c, s in scores.items() if s <= 0)
    prev_dead = st.get("dead", [])
    newly = [c for c in dead if c not in prev_dead and c not in MAIN_TIER_MAPPED]
    healed = [c for c in prev_dead
              if c not in dead and c not in MAIN_TIER_MAPPED and c in new_w]
    # 全灭告警也按跳变发（v5.1）：持续全灭不重复轰炸，恢复走 healed 分支
    all_dead = bool(dead) and len(dead) == len(tier)
    if all_dead:
        if not st.get("all_dead"):
            send_tg("🚨 *Opus %d 层全灭*：%s 探针全部失败\nweights: %s"
                    % (top_pri, ", ".join("#%d %s" % (c, names.get(c, ""))
                                          for c in dead), new_w))
    else:
        for c in newly:
            send_tg("⚠️ Opus 渠道判死：#%d %s → w1" % (c, names.get(c, "")))
        for c in healed:
            send_tg("✅ Opus 渠道恢复：#%d %s → w%d" % (c, names.get(c, ""), new_w[c]))
    st["all_dead"] = all_dead
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
