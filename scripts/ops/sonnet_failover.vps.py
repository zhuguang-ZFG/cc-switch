#!/usr/bin/env python3
"""Sonnet 主备探活 + priority 自动对调

**已废弃（2026-07-27）**：路由主排序键为 channels.priority，本脚本调整 abilities.priority 无效，保留仅供 --force 手动使用

背景 (2026-07-27):
- Claude Code 在 CLAUDE_CODE_MAX_CONTEXT_TOKENS=1048576 时自动给模型名追加
  `[1m]`（小写）。渠道须有 `[1m]` / `[1M]` ability + model_mapping 回基础模型。
- Sonnet 曾只有 #125 (vyceai) 一个渠道，它返回 HTTP 525 时 auto-mode 安全分类器
  直接不可用，且 NewAPI **未**自动 failover（525 疑不在其重试集）。
- 因此需要显式按探活结果调 priority，而不是依赖 NewAPI 自己切。

行为:
- 两边都活 → #125 主 (pri 35)，#63 备 (pri 25)
- 只有一边活 → 活的那个升主
- 两边都挂 → 不动 priority（避免把流量导向同样挂掉的渠道）

VPS: /opt/new-api/sonnet_failover.py
镜像: scripts/ops/sonnet_failover.vps.py
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

DB = "/opt/new-api/data/one-api.db"

# 主渠优先——vyceai 是真 Claude；#63 后端是 Kimi，属降级路径。
PRIMARY = 125  # vyceai-claude
BACKUP = 63  # fallback-claude-to-kimi

PRI_HIGH = 35
PRI_LOW = 25

# 各渠道用自己确实支持的模型探活。
PROBE_MODEL = {
    PRIMARY: "claude-sonnet-4-6",
    BACKUP: "claude-sonnet-5",
}

SONNET_LIKE = "model LIKE 'claude-sonnet%'"


def probe(conn: sqlite3.Connection, cid: int) -> tuple[str, str]:
    """直连上游探活，返回 (http_code, 摘要)。"""
    row = conn.execute("SELECT base_url, key FROM channels WHERE id=?", (cid,)).fetchone()
    if not row:
        return "MISSING", f"channel {cid} not found"

    base, key = row[0], row[1].split("\n")[0].strip()
    url = base.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": PROBE_MODEL[cid],
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }

    try:
        r = subprocess.run(
            [
                "curl", "-s", "--compressed", "-w", "\nHTTP:%{http_code}", url,
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {key}",
                "-d", json.dumps(payload),
                "--connect-timeout", "10",
            ],
            capture_output=True, text=True, timeout=25, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "probe exceeded 25s"
    except Exception as e:  # noqa: BLE001 - 探活失败即视为不可用
        return "ERR", str(e)[:90]

    code, body = "", ""
    for line in r.stdout.strip().split("\n"):
        if line.startswith("HTTP:"):
            code = line.replace("HTTP:", "")
        else:
            body = line
    return code, ("OK" if code == "200" else body[:90])


def set_priority(conn: sqlite3.Connection, cid: int, pri: int) -> None:
    conn.execute(
        f"UPDATE abilities SET priority=? WHERE channel_id=? AND {SONNET_LIKE}",
        (pri, cid),
    )


def show_routing(conn: sqlite3.Connection) -> None:
    print("\n=== Sonnet routing (higher pri wins) ===")
    rows = conn.execute(
        "SELECT model, channel_id, priority FROM abilities "
        f"WHERE {SONNET_LIKE} AND enabled=1 ORDER BY model, priority DESC"
    ).fetchall()
    names = {PRIMARY: "vyceai", BACKUP: "kimi-coding"}
    current = None
    for model, cid, pri in rows:
        if model != current:
            print(f"\n  {model}:")
            current = model
        print(f"    ch#{cid} {names.get(cid, cid)} pri={pri}")


def main() -> int:
    if "--force" not in sys.argv:
        print(
            "已废弃（2026-07-27）：本脚本默认不再执行。\n"
            "- 实证该 NewAPI fork 的路由主排序键是 channels.priority，而非 abilities.priority，\n"
            "  本脚本调整 abilities.priority 本来就无效。\n"
            "- channels.priority 主备已固定（#125=35 > #63=-20），vyceai 恢复后会自动回主渠，无需对调。\n"
            "- 如确需手动执行旧逻辑，请加 --force。"
        )
        return 0
    conn = sqlite3.connect(DB)

    print("=== Probe ===")
    results = {}
    for cid in (PRIMARY, BACKUP):
        code, note = probe(conn, cid)
        results[cid] = code == "200"
        print(f"  ch#{cid} {PROBE_MODEL[cid]}: {code} {note}")

    if results[PRIMARY]:
        hi, lo, why = PRIMARY, BACKUP, "primary healthy"
    elif results[BACKUP]:
        hi, lo, why = BACKUP, PRIMARY, "primary down, backup healthy"
    else:
        print("\nBoth Sonnet channels down — leaving priorities untouched.")
        show_routing(conn)
        conn.close()
        return 1

    print(f"\n{why} -> ch#{hi} primary (pri {PRI_HIGH}), ch#{lo} backup (pri {PRI_LOW})")
    set_priority(conn, hi, PRI_HIGH)
    set_priority(conn, lo, PRI_LOW)
    conn.commit()

    show_routing(conn)
    conn.close()

    print("\n改完需要: podman restart new-api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
