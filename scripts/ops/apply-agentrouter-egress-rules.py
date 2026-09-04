#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Re-apply agentrouter egress pin to Clash Verge runtime (idempotent).

背景（2026-09-04，runbook: docs/ops/agentrouter-waf-glm53-2026-09-04.md）：
agentrouter 双域名对家宽 IP 触发阿里云 WAF 全量挑战。修复 =
Clash mixed-port 7897 后按 DOMAIN-SUFFIX 把 air-outer.com/agentrouter.org
钉到 Agentrouter-EG 专属出口组（HK02直连/HK05原生 可穿透）。

Clash Verge Rev 重生成 runtime 配置（订阅更新/激活 profile）时会丢弃 runtime
注入；per-profile merge 文件（profiles/mc4PF6D8TBKv.yaml）是持久层但该 Verge
构建不主动重放。本脚本幂等补回：

  1. GET /rules 已含 air-outer.com → 什么都不做；
  2. 否则向 %APPDATA%/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml
     注入 Agentrouter-EG 组 + 2 条规则（顶格列表项缩进，与文件风格一致），
     先备份 clash-verge.yaml；
  3. PUT /configs?force=true 热重载并复核。

用法：py -V:Astral/CPython3.12.13 apply-agentrouter-egress-rules.py [--force-reload]
退出码：0 = 规则已在位或注入成功；1 = 注入失败（锚点/节点缺失等）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

CTRL = "http://127.0.0.1:9097"
SECRET = "set-your-secret"
VERGE_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "io.github.clash-verge-rev.clash-verge-rev",
)
RUNTIME = os.path.join(VERGE_DIR, "clash-verge.yaml")
BACKUP_SUFFIX = ".bak-agentrouter-egress"
RULE_SUFFIXES = ("air-outer.com", "agentrouter.org")
GROUP = "Agentrouter-EG"
# 首选节点 = 已验证穿透 WAF 的出口（38ms hysteria2）。其余候选为备选。
DEFAULT_NODE = "🇭🇰【亚洲】香港02丨直连"
NODES = [
    DEFAULT_NODE,
    "🇭🇰【亚洲】香港03丨直连",
    "🇯🇵【亚洲】日本02三网优化丨Vless 2x",
    "🇭🇰【亚洲】香港01丨V6【1x】",
    "🇭🇰【亚洲】香港05原生丨移动直连",
]

GROUP_LINES = [
    f"- name: {GROUP}",
    "  type: select",
    "  proxies:",
] + [f"    - {n}" for n in NODES]
RULE_LINES = [f"- DOMAIN-SUFFIX,{s},{GROUP}" for s in RULE_SUFFIXES]


def ctrl_get_rules() -> list[dict]:
    req = urllib.request.Request(
        f"{CTRL}/rules", headers={"Authorization": f"Bearer {SECRET}"}
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r).get("rules", [])


def ctrl_get_group() -> dict | None:
    req = urllib.request.Request(
        f"{CTRL}/proxies", headers={"Authorization": f"Bearer {SECRET}"}
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r).get("proxies", {}).get(GROUP)


def ctrl_reload() -> None:
    req = urllib.request.Request(
        f"{CTRL}/configs?force=true",
        data=json.dumps({"payload": ""}).encode(),
        headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req, timeout=30)


def ctrl_select(node: str) -> None:
    req = urllib.request.Request(
        f"{CTRL}/proxies/{urllib.parse.quote(GROUP)}",
        data=json.dumps({"name": node}).encode(),
        headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req, timeout=8)


def inject_runtime() -> bool:
    """Insert group+rules into clash-verge.yaml. Returns True if changed."""
    with open(RUNTIME, encoding="utf-8") as f:
        lines = f.read().splitlines()

    already = any(
        f"DOMAIN-SUFFIX,{s}," in l for l in lines for s in RULE_SUFFIXES
    )
    has_group = any(l.strip() == f"- name: {GROUP}" for l in lines)
    if already and has_group:
        return False

    gi = ri = None
    for i, l in enumerate(lines):
        # 顶格键 = 顶层锚点（嵌套在 prepend 等块内的带缩进键不算）
        if l.rstrip() == "proxy-groups:" and gi is None:
            gi = i
        if l.rstrip() == "rules:" and ri is None:
            ri = i
    if gi is None or ri is None:
        raise RuntimeError(f"anchors missing: proxy-groups@{gi} rules@{ri}")

    # 从后往前插，避免索引位移；group 插在列表头、规则插在 rules 列表头（最优先）
    if not already:
        lines[ri + 1 : ri + 1] = RULE_LINES
    if not has_group:
        lines[gi + 1 : gi + 1] = GROUP_LINES

    shutil.copy2(RUNTIME, RUNTIME + BACKUP_SUFFIX + time.strftime("-%Y%m%d-%H%M%S"))
    with open(RUNTIME, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-reload", action="store_true", help="即使规则在位也强制热重载")
    args = ap.parse_args()

    rules = ctrl_get_rules()
    present = [
        r["payload"]
        for r in rules
        if any(s in r["payload"].lower() for s in RULE_SUFFIXES)
    ]
    grp = ctrl_get_group()
    print(f"runtime rules total={len(rules)} agentrouter={present} group={'有' if grp else '无'}")

    if present and grp and not args.force_reload:
        now = grp.get("now")
        print(f"已在位，无需操作（当前出口：{now}）")
        return 0

    if not os.path.isfile(RUNTIME):
        print(f"runtime 配置不存在：{RUNTIME}")
        return 1

    changed = inject_runtime()
    print("注入：" + ("完成（已备份）" if changed else "跳过（文件已含）"))
    ctrl_reload()
    time.sleep(2)

    rules = ctrl_get_rules()
    present = [
        r["payload"]
        for r in rules
        if any(s in r["payload"].lower() for s in RULE_SUFFIXES)
    ]
    grp = ctrl_get_group()
    ok = len(present) == len(RULE_SUFFIXES) and grp is not None
    print(f"复核：rules={present} group={'有' if grp else '无'} → {'OK' if ok else 'FAIL'}")
    if ok and grp and grp.get("now") != DEFAULT_NODE:
        try:
            ctrl_select(DEFAULT_NODE)
            print(f"出口已选：{DEFAULT_NODE}")
        except Exception as e:  # noqa: BLE001
            print(f"选节点失败（可手动在面板切）：{e}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
