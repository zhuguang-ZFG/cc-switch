#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unified_router.py — NewAPI 统一路由控制器 v6 (2026-07-28)

替代 route_optimizer.py + autoweight.py + health_check.py 的调权部分。
一个脚本，一份状态，一套配置。

核心改进（vs route_optimizer v5）：
- P2C + Peak-EWMA 评分（引入 inflight 并发感知 + 峰值记忆）
- 断路器三态机（CLOSED/OPEN/HALF_OPEN）替代"降权但不摘渠"
- 真实流量延迟（logs 表 use_time）与探针双信号融合
- YAML 配置替代硬编码常量
- 统一状态文件 router_state.json

社区参考：quant67 P2C/EWMA, relay-pulse, LiteLLM Router, Envoy panic threshold.
Design: docs/plans/newapi-unified-router-v6-2026-07-28.md
"""
import json, math, os, re, sqlite3, subprocess, sys, time, urllib.request

DB = "/opt/new-api/data/one-api.db"
STATE = "/opt/new-api/data/router_state.json"
LOG = "/opt/new-api/data/unified_router.log"
CONFIG = "/opt/new-api/scripts/unified_router_config.yaml"
LOCK = "/opt/new-api/data/unified_router.lock"

sys.path.insert(0, "/opt/new-api")
try:
    from tg_notify import send_tg
except Exception:
    def send_tg(text, **kw):
        return False

TYPE_ANTHROPIC = 14
RESPONSES_CHANNELS = {142}
ERR_RE = re.compile(r"channel error \(channel #(\d+)(?:, status code: (\d+))?\): ([^\n]*)")

DEFAULTS = {
    "probe_timeout": 30, "log_window": "6m", "traffic_window_s": 360,
    "ewma_alpha": 0.4, "err_penalty": 3.0, "hysteresis": 5,
    "w_min_ok": 3, "w_max": 60, "w_dead": 1,
    "err_w_auth": 3.0, "err_w_conc": 0.5, "err_w_other": 2.0, "err_w_content": 0.0,
    "breaker": {"error_rate_threshold": 0.5, "min_requests": 5,
                "window_seconds": 120, "cooldown_seconds": 30, "panic_threshold": 0.7},
    "scoring": {"inflight_weight": 0.3, "inflight_estimate_window": 60,
                "peak_ewma_beta": 0.05, "real_traffic_ratio": 0.6},
}


# ═══════════════ Config & State ═══════════════

def load_config():
    cfg = dict(DEFAULTS)
    try:
        import yaml
        with open(CONFIG, encoding="utf-8") as f:
            yml = yaml.safe_load(f) or {}
        for k in ("probe_timeout", "log_window", "traffic_window_s", "ewma_alpha",
                   "err_penalty", "hysteresis", "w_min_ok", "w_max", "w_dead",
                   "err_w_auth", "err_w_conc", "err_w_other", "err_w_content"):
            if k in yml:
                cfg[k] = yml[k]
        for sub in ("breaker", "scoring"):
            if sub in yml:
                cfg[sub].update(yml[sub])
        cfg["tiers"] = yml.get("tiers", {})
    except Exception as e:
        log("WARN config load failed (%s), using defaults" % e)
        cfg["tiers"] = {}
    return cfg


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
        return {"ewma_ttft": {}, "peak_ttft": {}, "ewma_err": {},
                "breakers": {}, "dead": [], "backup_weights": {}}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)


# ═══════════════ TTFT Probes ═══════════════

def _probe_stream(url, headers, body, timeout):
    """Generic streaming TTFT: send POST, return seconds-to-first-chunk or None."""
    req = urllib.request.Request(url, data=body, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not (200 <= r.status < 300):
                return None
            for chunk in r:
                if chunk.strip():
                    return time.time() - t0
            return None
    except Exception:
        return None


def probe_channel(row, probe_model, timeout=30):
    """Dispatch probe by channel type. Returns TTFT seconds or None."""
    key = (row["key"] or "").split("\n")[0].strip()
    try:
        mm = json.loads(row["model_mapping"] or "{}")
    except Exception:
        mm = {}
    model = mm.get(probe_model, probe_model)
    try:
        hdr = json.loads(row["header_override"] or "{}")
    except Exception:
        hdr = {}
    ua = hdr.get("User-Agent") or "new-api-unified-router/6.0"
    base = row["base_url"].rstrip("/")
    cid = row["id"]

    if row["type"] == TYPE_ANTHROPIC:
        body = json.dumps({"model": model, "max_tokens": 8, "stream": True,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        h = {"content-type": "application/json", "x-api-key": key,
             "anthropic-version": "2023-06-01",
             "user-agent": "claude-cli/2.1.2 (external, cli)"}
        return _probe_stream(base + "/v1/messages", h, body, timeout)

    if cid in RESPONSES_CHANNELS:
        body = json.dumps({"model": model, "max_output_tokens": 8, "stream": True,
                           "input": "hi"}).encode()
        h = {"content-type": "application/json",
             "authorization": "Bearer " + key, "user-agent": ua}
        return _probe_stream(base + "/v1/responses", h, body, timeout)

    body = json.dumps({"model": model, "max_tokens": 8, "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    h = {"content-type": "application/json",
         "authorization": "Bearer " + key, "user-agent": ua}
    return _probe_stream(base + "/v1/chat/completions", h, body, timeout)


# ═══════════════ Error Classification ═══════════════

def channel_error_classes(ids, log_window):
    cls = {cid: {"auth": 0, "conc": 0, "other": 0, "content": 0} for cid in ids}
    try:
        out = subprocess.run(
            ["podman", "logs", "--since", log_window, "new-api"],
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


def weighted_err_count(c, cfg):
    return (cfg["err_w_auth"] * c["auth"] + cfg["err_w_conc"] * c["conc"]
            + cfg["err_w_other"] * c["other"] + cfg["err_w_content"] * c["content"])


# ═══════════════ Real Traffic Stats (NEW v6) ═══════════════

def real_traffic_stats(db, ids, model_family, window_s):
    """Real traffic p50/p90 + cache hit rate from logs table."""
    stats = {}
    cutoff = int(time.time()) - window_s
    ph = ",".join("?" * len(ids))
    q = ("SELECT channel_id, use_time, prompt_tokens,"
         " CAST(json_extract(other, '$.cache_tokens') AS INTEGER) AS ct"
         " FROM logs WHERE model_name LIKE ? AND created_at > ?"
         " AND channel_id IN (%s) AND type=2" % ph)
    by_chan = {}
    for r in db.execute(q, [model_family, cutoff] + ids):
        by_chan.setdefault(r[0], []).append(r)
    for cid, rs in by_chan.items():
        times = sorted(r[1] for r in rs)
        n = len(times)
        p50 = times[n // 2] if n else 0
        p90 = times[int(n * 0.9)] if n > 1 else (times[0] if times else 0)
        total_pt = sum(r[2] for r in rs)
        total_ct = sum(r[3] or 0 for r in rs)
        cache_ratio = (total_ct / total_pt * 100.0) if total_pt > 0 else 0.0
        stats[cid] = {"p50": p50, "p90": p90, "count": n, "cache_ratio": cache_ratio}
    return stats


def estimate_inflight(db, ids, model_family, window_s):
    """Estimate concurrent in-flight: (requests × avg_use_time) / window."""
    cutoff = int(time.time()) - window_s
    ph = ",".join("?" * len(ids))
    q = ("SELECT channel_id, COUNT(*) as n, AVG(use_time) as avg_t"
         " FROM logs WHERE model_name LIKE ? AND created_at > ?"
         " AND channel_id IN (%s) AND type=2 GROUP BY channel_id" % ph)
    inflight = {}
    for r in db.execute(q, [model_family, cutoff] + ids):
        inflight[r[0]] = round((r[1] * r[2]) / window_s, 1) if window_s > 0 else 0
    return inflight


# ═══════════════ Peak-EWMA Scoring (NEW v6) ═══════════════

def update_peak_ewma(st, cid, ttft, alpha, beta):
    """Peak-EWMA: tracks smoothed average + decaying peak (for spike memory)."""
    sk = str(cid)
    prev_e = st.setdefault("ewma_ttft", {}).get(sk, ttft)
    ewma = alpha * ttft + (1 - alpha) * prev_e
    st["ewma_ttft"][sk] = ewma

    prev_p = st.setdefault("peak_ttft", {}).get(sk, ttft)
    peak = max(ttft, prev_p * (1 - beta))
    st["peak_ttft"][sk] = peak
    return ewma, peak


def compute_score(ewma_ttft, peak_ttft, ewma_err, inflight, cfg):
    """P2C-style score: lower is better. Blends EWMA + peak + error + inflight."""
    sc = cfg["scoring"]
    err_pen = 1.0 + cfg["err_penalty"] * ewma_err
    infl_pen = 1.0 + sc["inflight_weight"] * inflight
    # Blended latency: 80% EWMA + 20% peak (spike memory)
    blended_lat = 0.8 * max(ewma_ttft, 0.3) + 0.2 * max(peak_ttft, 0.3)
    return blended_lat * err_pen * infl_pen


# ═══════════════ Circuit Breaker (NEW v6) ═══════════════

def breaker_transition(st, cid, probe_ok, err_rate, total_req, cfg):
    """3-state circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.
    Returns (state, effective_weight_multiplier)."""
    bk_cfg = cfg["breaker"]
    sk = str(cid)
    cur = st.setdefault("breakers", {}).get(sk, "CLOSED")
    now = time.time()

    if cur == "CLOSED":
        if (total_req >= bk_cfg["min_requests"]
                and err_rate > bk_cfg["error_rate_threshold"]):
            st["breakers"][sk] = "OPEN"
            st["_breaker_opened_ts"] = st.get("_breaker_opened_ts", {})
            st["_breaker_opened_ts"][sk] = now
            log("BREAKER #%d CLOSED→OPEN (err_rate=%.0f%%, %d reqs)" % (cid, err_rate * 100, total_req))
            return "OPEN", 0.0

    elif cur == "OPEN":
        opened_ts = st.get("_breaker_opened_ts", {}).get(sk, now)
        if now - opened_ts >= bk_cfg["cooldown_seconds"]:
            st["breakers"][sk] = "HALF_OPEN"
            log("BREAKER #%d OPEN→HALF_OPEN (cooldown elapsed)" % cid)
            return "HALF_OPEN", 0.1  # trickle traffic for probe
        return "OPEN", 0.0

    elif cur == "HALF_OPEN":
        if probe_ok:
            st["breakers"][sk] = "CLOSED"
            log("BREAKER #%d HALF_OPEN→CLOSED (probe ok)" % cid)
            return "CLOSED", 1.0
        else:
            st["breakers"][sk] = "OPEN"
            st["_breaker_opened_ts"][sk] = now
            log("BREAKER #%d HALF_OPEN→OPEN (probe fail)" % cid)
            return "OPEN", 0.0

    return cur, 1.0


def apply_panic_threshold(st, tier_ids, cfg):
    """If >70% channels OPEN, force all OPEN→HALF_OPEN (avoid total blackout)."""
    bk_cfg = cfg["breaker"]
    open_count = sum(1 for cid in tier_ids
                     if st.get("breakers", {}).get(str(cid)) == "OPEN")
    if len(tier_ids) > 0 and open_count / len(tier_ids) > bk_cfg["panic_threshold"]:
        for cid in tier_ids:
            if st.get("breakers", {}).get(str(cid)) == "OPEN":
                st["breakers"][str(cid)] = "HALF_OPEN"
        log("PANIC: %d/%d channels OPEN → forced HALF_OPEN" % (open_count, len(tier_ids)))
        send_tg("🟡 路由 panic: %d/%d 渠道熔断 → 全降 HALF_OPEN" % (open_count, len(tier_ids)))


# ═══════════════ Tier Processing ═══════════════

def process_tier(db, st, cfg, tier_name, tier_cfg, dry_run=False):
    """Process one tier: probe → score → breaker → weight → write DB."""
    model_family = tier_cfg.get("model_family", "claude-opus%")
    probe_model = tier_cfg.get("probe_model", "claude-opus-5")
    channel_ids = tier_cfg.get("channels", [])
    if not channel_ids:
        return {}

    ph = ",".join("?" * len(channel_ids))
    rows = db.execute(
        "SELECT id, name, base_url, key, weight, type, model_mapping, header_override"
        " FROM channels WHERE id IN (%s) AND status=1" % ph, channel_ids).fetchall()
    if not rows:
        log("tier %s: no live channels" % tier_name)
        return {}

    ids = [r[0] for r in rows]
    ph = ",".join("?" * len(ids))  # FIX: rebuild ph from filtered ids (not config channel_ids)
    names = {r[0]: r[1][:24] for r in rows}

    # ── Gather signals ──
    err_cls = channel_error_classes(ids, cfg["log_window"])
    errs = {cid: sum(c.values()) for cid, c in err_cls.items()}
    traffic = real_traffic_stats(db, ids, model_family, cfg["traffic_window_s"])
    inflight = estimate_inflight(db, ids, model_family,
                                 cfg["scoring"]["inflight_estimate_window"])

    # ── Success counts for error rate ──
    cutoff = int(time.time()) - cfg["traffic_window_s"]
    succ = {cid: 0 for cid in ids}
    for r in db.execute(
        "SELECT channel_id, COUNT(*) FROM logs WHERE model_name LIKE ?"
        " AND created_at > ? AND channel_id IN (%s) AND type=2 GROUP BY channel_id" % ph,
        [model_family, cutoff] + ids):
        succ[r[0]] = r[1]

    # ── Save backup weights on first run ──
    bw_key = tier_name
    if st["backup_weights"].get(bw_key) is None:
        st["backup_weights"][bw_key] = {str(r[0]): r[4] for r in rows}
        save_state(st)

    # ── Probe + score each channel ──
    scores = {}
    breaker_states = {}
    alpha = cfg["ewma_alpha"]
    beta = cfg["scoring"]["peak_ewma_beta"]
    real_ratio = cfg["scoring"]["real_traffic_ratio"]

    probe_overrides = tier_cfg.get("probe_model_override", {})

    for r in rows:
        cid, name = r[0], names[r[0]]
        row_dict = {"id": r[0], "key": r[3], "base_url": r[2], "type": r[5],
                    "model_mapping": r[6], "header_override": r[7]}

        chan_probe_model = probe_overrides.get(str(cid), probe_model)
        ttft = probe_channel(row_dict, chan_probe_model, cfg["probe_timeout"])
        probe_ok = ttft is not None

        # Error rate
        werr = weighted_err_count(err_cls[cid], cfg)
        total_req = errs[cid] + succ[cid]
        err_rate = werr / max(total_req, 1.0)
        prev_er = st.setdefault("ewma_err", {}).get(str(cid), err_rate)
        ew_r = alpha * err_rate + (1 - alpha) * prev_er
        st["ewma_err"][str(cid)] = ew_r

        # Breaker
        bk_state, bk_mult = breaker_transition(st, cid, probe_ok, ew_r, total_req, cfg)
        breaker_states[cid] = bk_state

        if not probe_ok:
            scores[cid] = 0.0
            c = err_cls[cid]
            log("ch#%-3d %-24s PROBE_FAIL br=%s (errs=%d a%d/c%d/o%d succ=%d)" % (
                cid, name, bk_state, errs[cid], c["auth"], c["conc"],
                c["other"], succ[cid]))
            continue

        # Peak-EWMA latency
        ew_t, peak_t = update_peak_ewma(st, cid, ttft, alpha, beta)

        # Blended latency: real traffic + probe
        rt = traffic.get(cid)
        if rt and rt["count"] >= 3:
            real_lat = real_ratio * rt["p50"] + (1 - real_ratio) * ew_t
        else:
            real_lat = ew_t

        # P2C score (lower = better)
        raw_score = compute_score(real_lat, peak_t, ew_r, inflight.get(cid, 0), cfg)
        scores[cid] = raw_score / max(bk_mult, 0.01)

        cache_note = " cache=%.0f%%" % rt["cache_ratio"] if rt else ""
        log("ch#%-3d %-24s ttft=%4.1fs ewma=%4.1fs peak=%4.1fs real=%4.1fs "
            "err=%.2f inflight=%.1f br=%s score=%.1f%s" % (
            cid, name, ttft, ew_t, peak_t, real_lat, ew_r,
            inflight.get(cid, 0), bk_state, raw_score, cache_note))

    # ── Panic threshold ──
    apply_panic_threshold(st, ids, cfg)

    # ── Normalize scores → weights (inverse: lower score = higher weight) ──
    inv = {cid: 1.0 / max(s, 0.01) for cid, s in scores.items() if s > 0}
    total_inv = sum(inv.values())
    new_w = {}
    for cid in ids:
        if cid not in inv or total_inv == 0:
            new_w[cid] = cfg["w_dead"]
        else:
            w = round(100.0 * inv[cid] / total_inv)
            w = max(cfg["w_min_ok"], min(cfg["w_max"], w))
            # Breaker override
            bk = st.get("breakers", {}).get(str(cid), "CLOSED")
            if bk == "OPEN":
                w = 0
            elif bk == "HALF_OPEN":
                w = min(w, 2)  # trickle
            new_w[cid] = w

    # ── Mapped channel margin gate ──
    mapped = set(tier_cfg.get("mapped_channels", []))
    if mapped:
        best_native = max((scores[c] for c in ids if c not in mapped
                          and scores.get(c, 0) > 0), default=0)
        margin = tier_cfg.get("mapped_margin", 1.3)
        cap = tier_cfg.get("mapped_cap", 25)
        for c in ids:
            if c not in mapped or scores.get(c, 0) <= 0:
                continue
            if best_native > 0 and scores[c] < best_native * margin:
                new_w[c] = 0
            else:
                new_w[c] = min(cap, new_w[c])

    # ── Apply to DB (with hysteresis) ──
    cur_w = {r[0]: r[4] for r in rows}
    changed = {c: new_w[c] for c in new_w if abs(new_w[c] - cur_w.get(c, 0)) >= cfg["hysteresis"]}
    if changed and not dry_run:
        for cid, w in new_w.items():
            db.execute("UPDATE channels SET weight=? WHERE id=?", (w, cid))
            db.execute("UPDATE abilities SET weight=? WHERE channel_id=? AND model LIKE ?",
                       (w, cid, model_family))
        db.commit()
        log("APPLY [%s] weights: %s" % (tier_name, new_w))
    elif dry_run:
        log("DRY-RUN [%s] would apply: %s (cur=%s)" % (tier_name, new_w, cur_w))
    else:
        log("[%s] no change (hysteresis) weights=%s" % (tier_name, new_w))

    # ── TG alerts for dead/recovery ──
    dead = sorted(c for c, s in scores.items() if s <= 0)
    prev_dead = st.get("dead", [])
    newly_dead = [c for c in dead if c not in prev_dead and c not in mapped]
    healed = [c for c in prev_dead if c not in dead and c not in mapped]
    for c in newly_dead:
        send_tg("⚠️ [%s] 渠道判死：#%d %s → w0" % (tier_name, c, names.get(c, "")))
    for c in healed:
        send_tg("✅ [%s] 渠道恢复：#%d %s → w%d" % (tier_name, c, names.get(c, ""), new_w.get(c, 0)))
    st["dead"] = dead

    return new_w


# ═══════════════ Main ═══════════════

def _acquire_lock():
    import fcntl
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another unified_router run holds the lock, exiting")
        sys.exit(0)
    return fd


def main():
    dry_run = "--dry-run" in sys.argv
    restore = "--restore" in sys.argv
    cfg = load_config()
    st = load_state()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    if restore:
        for tier_name, bw in st.get("backup_weights", {}).items():
            for cid, w in bw.items():
                db.execute("UPDATE channels SET weight=? WHERE id=?", (w, int(cid)))
            db.commit()
        log("restored all backup weights: %s" % st.get("backup_weights", {}))
        db.close()
        return

    all_weights = {}
    for tier_name, tier_cfg in cfg.get("tiers", {}).items():
        if tier_name == "sonnet_chain":
            continue  # sonnet uses swap logic, handled separately for now
        log("──── tier: %s ────" % tier_name)
        w = process_tier(db, st, cfg, tier_name, tier_cfg, dry_run)
        all_weights[tier_name] = w

    save_state(st)
    db.close()
    log("═══ unified_router cycle complete (dry_run=%s) ═══" % dry_run)


if __name__ == "__main__":
    _lock_fd = _acquire_lock()
    main()
