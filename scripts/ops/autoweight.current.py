#!/usr/bin/env python3
"""NewAPI 渠道自动调权：基于 kiro-guard /metrics 延迟和成功率。
cron 每 2 小时执行。

规则：
- p50 < 10s 且成功率 > 90%: 加权（+5, 上限 50）
- p50 > 25s 或成功率 < 70%: 降权（-8, 下限 5）
- 单次调整 |delta| <= 15
- 冷却：同渠道 3h 内不重复调
- 不碰 status=2 的渠道
- 同渠道有多个 guard 时取加权平均 metrics
"""
import json, os, subprocess, sys, time, urllib.request

DB = "/opt/new-api/data/one-api.db"
LOG = "/opt/new-api/autoweight.log"
COOLDOWN_FILE = "/opt/new-api/autoweight-cooldown.json"
COOLDOWN_HOURS = 3

# channel_id -> list of guard ports (aggregate metrics across ports)
CHANNEL_GUARDS = {
    9:   [8400, 8403],
    10:  [8401, 8404],
    20:  [8405],
    118: [8410],
}

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def sql(q):
    return subprocess.check_output(["sqlite3", DB, q], text=True, timeout=10).strip()

def get_metrics(port):
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5)
        return json.loads(resp.read().decode())
    except Exception:
        return None

def aggregate_metrics(ports):
    total_ok, total_soft, total_hard = 0, 0, 0
    all_p50 = []
    for port in ports:
        m = get_metrics(port)
        if not m:
            continue
        ok = m.get("ok", 0)
        soft_d = m.get("soft", {})
        hard_d = m.get("hard", {})
        soft = sum(soft_d.values()) if isinstance(soft_d, dict) else int(soft_d or 0)
        hard = sum(hard_d.values()) if isinstance(hard_d, dict) else int(hard_d or 0)
        total_ok += ok
        total_soft += soft
        total_hard += hard
        lat = m.get("upstream_latency", {})
        p50 = lat.get("p50_ms")
        if p50 is not None:
            all_p50.append(p50)
    total = total_ok + total_soft + total_hard
    if total < 10:
        return None
    success_rate = total_ok / total
    avg_p50 = sum(all_p50) / len(all_p50) if all_p50 else 99999
    return {"ok": total_ok, "total": total, "rate": success_rate, "p50": avg_p50}

def load_cooldown():
    try:
        with open(COOLDOWN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_cooldown(cd):
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(cd, f)

now = time.time()
cooldown = load_cooldown()
changes = []

for cid, ports in CHANNEL_GUARDS.items():
    cd_key = str(cid)
    last = cooldown.get(cd_key, 0)
    if now - last < COOLDOWN_HOURS * 3600:
        continue

    current_w = sql(f"SELECT weight FROM channels WHERE id={cid} AND status=1")
    if not current_w:
        continue
    w = int(current_w)

    agg = aggregate_metrics(ports)
    if not agg:
        continue

    p50 = agg["p50"]
    success_rate = agg["rate"]

    delta = 0
    reason = ""
    if p50 < 10000 and success_rate > 0.90:
        delta = min(5, 50 - w)
        reason = f"fast+reliable (p50={p50:.0f}ms rate={success_rate:.0%})"
    elif p50 > 25000 or success_rate < 0.70:
        delta = max(-8, 5 - w)
        reason = f"slow/unreliable (p50={p50:.0f}ms rate={success_rate:.0%})"

    if delta == 0:
        continue

    new_w = w + delta
    sql(f"UPDATE channels SET weight={new_w} WHERE id={cid}")
    cooldown[cd_key] = now
    changes.append(f"#{cid} w={w}->{new_w} ({reason})")
    log(f"#{cid} w={w}->{new_w} ({reason})")

save_cooldown(cooldown)

if changes:
    subprocess.run(["podman", "restart", "new-api"], timeout=30)
    log(f"NewAPI restarted, {len(changes)} weight changes")
    try:
        secrets_path = "/opt/new-api/secrets.json"
        if os.path.exists(secrets_path):
            with open(secrets_path) as f:
                secrets = json.load(f)
            token = secrets.get("tg_bot_token", "")
            chat = secrets.get("tg_chat_id", "")
            if token and chat:
                msg = "AutoWeight:\n" + "\n".join(changes)
                body = json.dumps({"chat_id": chat, "text": msg}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                proxy = urllib.request.ProxyHandler({"https": "http://127.0.0.1:7890"})
                urllib.request.build_opener(proxy).open(req, timeout=10)
    except Exception:
        pass
else:
    log("no weight changes needed")
