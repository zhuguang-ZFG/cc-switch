#!/usr/bin/env python3
"""ch3-100xlabs-reprobe — ch3 (baibei-100xlabs) 复探回捞哨兵（一次性进程，计划任务每 15min 触发）。

背景（2026-10-06）：100xlabs 公益池整体垮塌——18 发直连探针仅 1 发真内容
（5.6%），其余空 200/502，6/6 key 全 degraded，fable 同病。ch3 是
claude-opus-5-5 / claude-fable-5-1 / claude-fable-5.1 三模型的唯一腿，
已按用户授权 fail-closed 禁用（503 "No available channel" 秒回，替代静默空回
烧上下文——事故窗口见过 2 发各 2.1M quota 的计费空回）。

本哨兵职责：池恢复后自动回捞。每轮：
1. 管理 API 读 ch3；status==1 → 直接退出（幂等稳态；也覆盖人工回捞）。
2. status∈(2,3) → 从本地 DB（SSOT，ro）取 key，轮换抽 2 个 key × 2 发流式探针。
3. 4/4 全部"200 + 正文 + usage + 正常收尾"才 POST status=1 回捞（单发成功在
   垮塌期有约 6% 假阳性，4/4 把偶发成功挡在门外）；回捞后双重复核：
   GET status==1 + abilities 三行 enabled==1（禁用会把它们翻 0，半开检出后
   先 /api/channel/fix 补救，仍坏则告警），全部通过才 Telegram 报成功。
4. 部分成功/全失败 → 只记日志与 state，不动配置、不刷群。
退出语义：0 = 无需动作或仍 down；1 = 管理 API/DB 不可用或回捞失败（fail-safe，
状态不明时不做任何 mutation）。

注意：若日后 ch3 因**其他原因**被禁用且不希望自动回捞，删除计划任务
"NewAPI ch3 100xlabs Reprobe" 即可（本脚本无其他禁用原因感知）。
凭据读 ~/.omp/guardian/secrets.json，key 不落日志、不回显。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

GUARDIAN_DIR = Path.home() / ".omp" / "guardian"
SECRETS_FILE = GUARDIAN_DIR / "secrets.json"
STATE_FILE = GUARDIAN_DIR / "ch3-reprobe-state.json"
LOG_FILE = GUARDIAN_DIR / "ch3-reprobe.log"
LOCK_FILE = GUARDIAN_DIR / "ch3-reprobe.lock"
NEWAPI_DB = Path(os.environ.get("NEWAPI_DB", str(Path.home() / ".new-api-local" / "new-api.db")))

CHANNEL_ID = 3
PROBE_MODEL = "claude-opus-5-5"  # 池内主模型；fable 同池同上游，同恢复信号
PROBE_KEYS_PER_RUN = 2           # 轮换抽 key，6 key 约 3 轮全覆盖
PROBE_ATTEMPTS = 2               # 每 key 尝试次数；回捞门禁 = 全部成功
PROBE_TIMEOUT = 45               # 垮塌期实测 18-63s；45s 截断坏腿
PROBE_INTERVAL = 3
LOCK_STALE_SECONDS = 600         # 最坏一轮 ~4*(45+3)≈200s；10min 陈旧即接管
MAX_LOG_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 64 * 1024

try:
    _SECRETS = json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig"))
except (OSError, ValueError):
    _SECRETS = {}

NEWAPI_BASE = str(_SECRETS.get("newapi_base", "http://127.0.0.1:3002")).rstrip("/")
NEWAPI_TOKEN = str(_SECRETS.get("newapi_token", ""))
NEWAPI_USER = str(_SECRETS.get("newapi_user", "1"))
TELEGRAM_TOKEN = str(_SECRETS.get("telegram_token", ""))
TELEGRAM_CHAT_ID = str(_SECRETS.get("telegram_chat_id", ""))


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            LOG_FILE.write_text(LOG_FILE.read_text(encoding="utf-8", errors="replace")[-MAX_LOG_BYTES // 4 :], encoding="utf-8")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"state 写入失败（非致命）: {e}")


def acquire_lock() -> bool:
    """单实例：锁新鲜则放弃；陈旧（前一轮崩溃残留）则接管。"""
    for attempt in (0, 1):
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
            except OSError:
                age = LOCK_STALE_SECONDS + 1
            if age > LOCK_STALE_SECONDS:
                try:
                    LOCK_FILE.unlink()
                except OSError:
                    pass
                continue
            return False
        except OSError:
            return attempt == 1
    return False


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


def is_disabled(status) -> bool:
    """2=手动禁用，3=NewAPI 自动禁用——同一死池语义，都在回捞范围内。"""
    return status in (2, 3)


def classify_probe(status, raw: bytes) -> tuple[bool, str]:
    """200 本身不算成功：必须见正文增量 + usage + 正常收尾（事故口径）。"""
    if status != 200:
        return False, f"HTTP {status}"
    if len(raw) > MAX_RESPONSE_BYTES:
        return False, "response exceeds probe size limit"
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        return False, "undecodable body"
    if '"type":"error"' in text or '"type": "error"' in text:
        return False, "SSE error event"
    has_content = '"content_block_delta"' in text and '"text_delta"' in text
    has_usage = '"usage"' in text
    has_stop = '"message_delta"' in text or '"message_stop"' in text
    if has_content and has_usage and has_stop:
        return True, "HTTP 200 with completed text"
    missing = [n for n, ok in (("content", has_content), ("usage", has_usage), ("stop", has_stop)) if not ok]
    return False, f"empty/incomplete 200 (missing {','.join(missing)})"

def abilities_enabled() -> tuple[bool, str]:
    """回捞完整性：禁用会把 abilities.enabled 翻 0（2026-10-06 实测）；status=1
    但 abilities 未回翻 = 半开（模型仍无腿），必须检出。"""
    try:
        conn = sqlite3.connect(f"file:{NEWAPI_DB}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT model, enabled FROM abilities WHERE channel_id=?", (CHANNEL_ID,)
            ).fetchall()
        finally:
            conn.close()  # with 只管事务不关连接；Windows 下残留文件锁
    except (OSError, sqlite3.Error) as e:
        return False, f"DB 读取失败: {e}"
    if not rows:
        return False, "无 abilities 行"
    bad = [str(m) for m, en in rows if en != 1]
    if bad:
        return False, f"disabled: {','.join(bad)}"
    return True, f"{len(rows)} rows enabled"


def should_reenable(verdicts: list[bool]) -> bool:
    """回捞门禁：非空且全部成功。垮塌期单发假阳性 ~6%，4/4 误捞概率 ~1e-5。"""
    return bool(verdicts) and all(verdicts)


def pick_keys(keys: list[str], cursor: int, n: int) -> list[str]:
    """轮换抽取，跨轮覆盖全部 key（避免死 key 恰好常驻探针位）。"""
    if not keys:
        return []
    n = min(n, len(keys))
    return [keys[(cursor + i) % len(keys)] for i in range(n)]


def api_request(method: str, path: str, data: dict | None = None, timeout: int = 15) -> dict | None:
    req = urllib.request.Request(
        f"{NEWAPI_BASE}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {NEWAPI_TOKEN}", "New-Api-User": NEWAPI_USER, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"NewAPI {method} {path} 失败: {e}")
        return None


def probe_upstream(key: str) -> tuple[bool, str]:
    body = json.dumps({
        "model": PROBE_MODEL, "max_tokens": 16, "stream": True,
        "messages": [{"role": "user", "content": "reply with exactly: pong"}],
    }).encode()
    req = urllib.request.Request("https://sub.100xlabs.space/v1/messages", data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", key)
    req.add_header("authorization", f"Bearer {key}")
    req.add_header("anthropic-version", "2023-06-01")
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            status = resp.status
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return classify_probe(status, raw)


def load_channel_keys() -> list[str]:
    """从本地 DB（SSOT）读 key——容忍 key 轮换；只读，不落日志。"""
    try:
        conn = sqlite3.connect(f"file:{NEWAPI_DB}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT key FROM channels WHERE id=?", (CHANNEL_ID,)).fetchone()
        finally:
            conn.close()  # 同上：显式关闭，避免 Windows 文件锁
    except (OSError, sqlite3.Error) as e:
        log(f"DB 读取失败: {e}")
        return []
    if not row or not row[0]:
        return []
    return [k.strip() for k in str(row[0]).replace("\\n", "\n").split("\n") if k.strip()]


def send_telegram(title: str, message: str, level: str = "info") -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram 未配置，跳过告警")
        return
    icons = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "success": "✅"}
    text = f"{icons.get(level, 'ℹ️')} <b>{title}</b>\n{message}"
    body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                log(f"Telegram 发送失败: HTTP {resp.status}")
    except Exception as e:
        log(f"Telegram 发送失败: {e}")


def main() -> int:
    if not acquire_lock():
        log("上一轮仍在运行，本轮跳过")
        return 0
    try:
        ch = api_request("GET", f"/api/channel/{CHANNEL_ID}")
        if not ch or not ch.get("success") or not isinstance(ch.get("data"), dict):
            return 1  # 状态不明，fail-safe 不动
        status = ch["data"].get("status")
        if not is_disabled(status):
            return 0  # 已启用（含人工回捞），稳态静默
        state = load_state()
        state.setdefault("disabled_since", datetime.now().isoformat(timespec="seconds"))
        keys = load_channel_keys()
        if not keys:
            log("DB 无 ch3 key，无法复探")
            return 1
        picked = pick_keys(keys, int(state.get("key_cursor", 0)), PROBE_KEYS_PER_RUN)
        state["key_cursor"] = (int(state.get("key_cursor", 0)) + PROBE_KEYS_PER_RUN) % max(len(keys), 1)
        verdicts: list[bool] = []
        details: list[str] = []
        for key in picked:
            for attempt in range(PROBE_ATTEMPTS):
                ok, detail = probe_upstream(key)
                verdicts.append(ok)
                details.append(f"{'ok' if ok else 'fail'}({detail})")
                if attempt + 1 < PROBE_ATTEMPTS:
                    time.sleep(PROBE_INTERVAL)
        ok_count = sum(verdicts)
        log(f"ch3 复探 {ok_count}/{len(verdicts)}: {' '.join(details)}")
        state["last_verdicts"] = {"at": datetime.now().isoformat(timespec="seconds"),
                                  "ok": ok_count, "total": len(verdicts)}
        if not should_reenable(verdicts):
            save_state(state)
            return 0
        res = api_request("POST", f"/api/channel/{CHANNEL_ID}/status", {"status": 1})
        if not res or not res.get("success"):
            log(f"回捞失败: status API 返回 {res}")
            save_state(state)
            send_telegram("ch3 回捞失败", "100xlabs 复探通过但 status API 回捞失败，请人工检查", "error")
            return 1
        verify = api_request("GET", f"/api/channel/{CHANNEL_ID}")
        if not verify or verify.get("data", {}).get("status") != 1:
            log("回捞复核失败: status 未切回 1")
            save_state(state)
            send_telegram("ch3 回捞复核失败", "status API 成功但复核 status≠1，请人工检查", "error")
            return 1
        ab_ok, ab_detail = abilities_enabled()
        if not ab_ok:
            log(f"abilities 未同步启用（{ab_detail}），尝试 /api/channel/fix")
            api_request("POST", "/api/channel/fix", {})
            ab_ok, ab_detail = abilities_enabled()
        if not ab_ok:
            log(f"回捞半开: status=1 但 abilities 异常（{ab_detail}）")
            save_state(state)
            send_telegram("ch3 回捞半开",
                          f"status=1 但 abilities 未启用（{ab_detail}），/api/channel/fix 未修复，请人工检查", "error")
            return 1
        log(f"abilities 复核通过: {ab_detail}")
        since = state.get("disabled_since", "?")
        state.pop("disabled_since", None)
        save_state(state)
        log("ch3 已回捞（status=1）")
        send_telegram("ch3 已自动回捞",
                      f"100xlabs 池复探 {ok_count}/{len(verdicts)} 通过，ch3(baibei-100xlabs) 已重新启用\n"
                      f"abilities: {ab_detail}\n"
                      f"禁用起于: {since}\n模型: claude-opus-5-5 / fable-5-1 / fable-5.1", "success")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
