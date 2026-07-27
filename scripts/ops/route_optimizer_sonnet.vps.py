#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_optimizer_sonnet.py — Sonnet 兜底链层间自动换序 (v3, 2026-07-27).

由 route_optimizer.py 的 __main__ 在同一 cron (*/5) 内调用。对各 Sonnet 兜底
渠打 TTFT 探针（按渠道 type 自动选 OpenAI/Anthropic 格式，模型取
model_mapping 的映射值），叠加容器日志错误率 EWMA 打分；当下层分数超过上层
SONNET_MARGIN 倍（或上层判死）时交换一层——每次运行最多交换一对相邻层，
渐进收敛，防抖动。

安全约束:
- 只改 priority（channels + abilities 双写），不碰 status/models/weight
- 原始 priority 备份在 state；`--restore-sonnet` 一键回滚
- `--sonnet-dry` 只探测打分不落库（部署验证用）
- 每次换序发 TG 通知
"""
import json
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/opt/new-api/scripts")

import route_optimizer as ro

SONNET_GROUP = [63, 133, 129, 134]  # #132 work.freemodel 拒绝 Claude Code，已摘渠移出
SONNET_REQ_MODEL = "claude-sonnet-4-6"
SONNET_FAMILY = "claude-sonnet%"
SONNET_MARGIN = 1.5
SONNET_PROBE_TIMEOUT = 20
TYPE_ANTHROPIC = 14


def _mapped(row, req_model):
    try:
        mm = json.loads(row["model_mapping"] or "{}")
    except Exception:
        mm = {}
    return mm.get(req_model, req_model)


def _hdr_override(row):
    try:
        h = json.loads(row["header_override"] or "{}")
    except Exception:
        h = {}
    return {str(k): str(v) for k, v in h.items()}


def _stream_ttft(req):
    """流式 TTFT 秒数。None=死/被拒（含响应头都不来）；
    SONNET_PROBE_TIMEOUT=200 已收到但首块超时（慢但活着）。"""
    import socket
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=SONNET_PROBE_TIMEOUT)
    except Exception:
        return None
    with r:
        if not (200 <= r.status < 300):
            return None
        try:
            for chunk in r:
                if chunk.strip():
                    return time.time() - t0
            return None
        except (socket.timeout, TimeoutError):
            return float(SONNET_PROBE_TIMEOUT)


def _probe_openai(base_url, key, model, ua):
    body = json.dumps({"model": model, "max_tokens": 8, "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions", data=body,
        headers={"content-type": "application/json",
                 "authorization": "Bearer " + key,
                 "user-agent": ua})
    return _stream_ttft(req)


def _probe_anthropic(base_url, key, model, hdrs):
    body = json.dumps({"model": model, "max_tokens": 8, "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    h = {"content-type": "application/json", "x-api-key": key,
         "anthropic-version": "2023-06-01",
         "user-agent": "claude-cli/2.1.2 (external, cli)"}
    h.update(hdrs)
    req = urllib.request.Request(base_url.rstrip("/") + "/v1/messages",
                                 data=body, headers=h)
    return _stream_ttft(req)


def _probe_one(row):
    cid = row["id"]
    model = _mapped(row, SONNET_REQ_MODEL)
    hdrs = _hdr_override(row)
    # 多 key 渠（换行分隔）只取首 key，防止非法头导致探针永久失败
    key = (row["key"] or "").split("\n")[0].strip()
    if row["type"] == TYPE_ANTHROPIC:
        return cid, _probe_anthropic(row["base_url"], key, model, hdrs)
    ua = hdrs.get("User-Agent", "new-api-route-optimizer/3.0")
    return cid, _probe_openai(row["base_url"], key, model, ua)


def sonnet_main():
    dry = "--sonnet-dry" in sys.argv
    restore = "--restore-sonnet" in sys.argv
    st = ro.load_state()
    db = sqlite3.connect(ro.DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id,name,type,base_url,key,priority,model_mapping,header_override FROM channels"
        " WHERE status = 1 AND id IN (%s)" % ",".join(map(str, SONNET_GROUP))
    ).fetchall()
    if len(rows) < 2:
        ro.log("sonnet: fewer than 2 live channels, skip")
        db.close()
        return

    if restore:
        bp = st.get("sonnet_backup_pri") or {}
        for cid, p in bp.items():
            db.execute("UPDATE channels SET priority=? WHERE id=?", (p, int(cid)))
            db.execute("UPDATE abilities SET priority=? WHERE channel_id=?",
                       (p, int(cid)))
        db.commit()
        ro.log("sonnet: restored priorities %s" % bp)
        db.close()
        return

    if st.get("sonnet_backup_pri") is None:
        st["sonnet_backup_pri"] = {str(r["id"]): r["priority"] for r in rows}

    ids = [r["id"] for r in rows]
    succ = {cid: 0 for cid in ids}
    q = ("SELECT channel_id, COUNT(*) n FROM logs WHERE model_name LIKE ?"
         " AND created_at > ? AND channel_id IN (%s) GROUP BY channel_id"
         % ",".join("?" * len(ids)))
    for r in db.execute(q, [SONNET_FAMILY, int(time.time()) - ro.TRAFFIC_WINDOW_S] + ids):
        succ[r["channel_id"]] = r["n"]
    errs = ro.channel_errors(ids)

    with ThreadPoolExecutor(max_workers=len(rows)) as ex:
        probed = dict(ex.map(_probe_one, rows))

    scores = {}
    for row in rows:
        cid = row["id"]
        ttft = probed.get(cid)
        if ttft is None:
            scores[cid] = 0.0
            ro.log("sonnet probe FAIL ch#%d %s (errs=%d succ=%d)"
                   % (cid, row["name"][:26], errs[cid], succ[cid]))
            continue
        prev_t = st.setdefault("ewma_ttft", {}).get(str(cid), ttft)
        ew_t = ro.EWMA_ALPHA * ttft + (1 - ro.EWMA_ALPHA) * prev_t
        st["ewma_ttft"][str(cid)] = ew_t
        rate = errs[cid] / (errs[cid] + succ[cid] + 1.0)
        prev_r = st.setdefault("ewma_err", {}).get(str(cid), rate)
        ew_r = ro.EWMA_ALPHA * rate + (1 - ro.EWMA_ALPHA) * prev_r
        st["ewma_err"][str(cid)] = ew_r
        lat = 0.5 * ew_t + 0.5 * ttft
        quality = 1.0 / (1.0 + ro.ERR_PENALTY * ew_r)
        scores[cid] = quality / max(lat, 0.3)
        ro.log("sonnet ch#%-3d %-24s ttft=%5.1fs lat=%5.1fs err=%.2f q=%.2f score=%.3f"
               % (cid, row["name"][:24], ttft, lat, ew_r, quality, scores[cid]))

    order = [r["id"] for r in sorted(rows, key=lambda r: -r["priority"])]
    names = {r["id"]: r["name"] for r in rows}
    swap = None
    for i in range(len(order) - 1):
        a, b = order[i], order[i + 1]
        sa, sb = scores.get(a, 0.0), scores.get(b, 0.0)
        if sb > 0 and (sa <= 0 or sb > sa * SONNET_MARGIN):
            swap = (a, b, sa, sb)
            order[i], order[i + 1] = b, a
            break

    if swap is None:
        ro.log("sonnet: no swap, order=%s scores=%s"
               % (order, {c: round(s, 3) for c, s in scores.items()}))
    elif dry:
        ro.log("sonnet DRY would-swap: %s order->%s" % (swap, order))
    else:
        a, b, sa, sb = swap
        ladder = sorted((r["priority"] for r in rows), reverse=True)
        for pos, cid in enumerate(order):
            db.execute("UPDATE channels SET priority=? WHERE id=?",
                       (ladder[pos], cid))
            db.execute("UPDATE abilities SET priority=? WHERE channel_id=?",
                       (ladder[pos], cid))
        db.commit()
        ro.send_tg("🔀 Sonnet 兜底换序：#%d %s ↑ 超过 #%d %s（score %.3f vs %.3f）"
                   % (b, names.get(b, ""), a, names.get(a, ""), sb, sa))
        ro.log("sonnet SWAP a=%d b=%d sa=%.3f sb=%.3f order=%s" % (a, b, sa, sb, order))

    ro.save_state(st)
    db.close()


if __name__ == "__main__":
    sonnet_main()
