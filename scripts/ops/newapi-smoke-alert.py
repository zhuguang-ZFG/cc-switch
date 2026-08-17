"""Send bounded Telegram alerts for the scheduled NewAPI smoke gate.

The smoke task already exits non-zero on drift, but Task Scheduler alone is a
silent signal.  This bridge alerts only on a healthy/failed transition and
retries delivery on the next run when Telegram is unavailable.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path


DEPLOY_DIR = Path.home() / ".new-api-local"
GUARDIAN_DIR = Path.home() / ".omp" / "guardian"
STATE_FILE = DEPLOY_DIR / "dx-smoke-alert-state.json"
LOG_FILE = Path(__file__).resolve().parents[2] / ".tmp-newapi-dx-ops.log"


def read_state() -> dict:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(f".tmp-{time.time_ns()}")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def latest_summary() -> str:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "smoke exited non-zero; log unavailable"
    for line in reversed(lines):
        if "summary:" in line:
            return line[-900:]
    return "smoke exited non-zero; summary missing"


def load_guardian():
    path = Path(__file__).with_name("guardian.py")
    spec = importlib.util.spec_from_file_location("guardian_for_smoke_alert", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load guardian.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def send_alert(failed: bool, summary: str) -> bool:
    secrets = json.loads((GUARDIAN_DIR / "secrets.json").read_text(encoding="utf-8-sig"))
    guardian = load_guardian()
    bot = guardian.TelegramBot(
        secrets.get("telegram_token", ""),
        str(secrets.get("telegram_chat_id", "")),
        secrets.get("telegram_proxy", ""),
    )
    if failed:
        return bool(
            bot.send_alert(
                "NewAPI smoke failed", guardian._html_escape(summary), "error"
            )
        )
    return bool(bot.send_alert("NewAPI smoke recovered", "scheduled gate is healthy", "success"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exit-code", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    failed = args.exit_code != 0
    current = "failed" if failed else "healthy"
    state = read_state()
    previous = state.get("status")
    should_alert = (failed and previous != "failed") or (
        not failed and previous == "failed"
    )
    summary = latest_summary() if failed else "scheduled gate is healthy"
    if args.dry_run:
        print(f"status={current} previous={previous} alert={should_alert} summary={summary[:200]}")
        return 0

    delivered = True
    if should_alert:
        try:
            delivered = send_alert(failed, summary)
        except (OSError, ValueError, RuntimeError) as error:
            delivered = False
            print(f"smoke alert delivery failed: {error}")

    # A failed transition is persisted only after successful delivery, so an
    # unavailable Telegram endpoint is retried on the next scheduled run.
    if not should_alert or delivered:
        write_state({"status": current, "updated_at": int(time.time())})
    return 0 if delivered else 1


if __name__ == "__main__":
    raise SystemExit(main())
