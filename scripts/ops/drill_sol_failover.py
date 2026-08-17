#!/usr/bin/env python3
"""Prove ch92-to-ch91 failover with guaranteed status/posture restoration."""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path


FAILOVER_TEXT = "CH91-FAILOVER-OK"
RECOVERY_TEXT = "CH92-RECOVERY-OK"


def load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"ch{channel_id} status restore/update failed: HTTP {response_status}")


def current_status(db_path: Path, channel_id: int) -> int:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        row = connection.execute(
            "SELECT status FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"ch{channel_id} is missing")
    return int(row[0])


def require_probe(verifier, smoke, key: str, expected: str, channel_id: int, last_id: int):
    metrics = verifier.aggregate_probe_metrics(
        smoke, key, expected_text=expected, timeout=120
    )
    if (
        metrics.text != expected
        or not metrics.done
        or metrics.prompt_tokens <= 0
        or metrics.completion_tokens <= 0
    ):
        raise RuntimeError(f"semantic/usage contract failed for expected ch{channel_id}")
    log_row = verifier.latest_log_after(Path(smoke.NEWAPI_DB).resolve(), last_id)
    if log_row is None or int(log_row[1]) != channel_id:
        actual = None if log_row is None else log_row[1]
        raise RuntimeError(f"expected channel_id={channel_id}, got {actual}")
    return metrics, log_row


def restore_baseline(
    posture,
    smoke,
    headers: dict[str, str],
    db_path: Path,
    original: dict,
    original_status: int,
    cache_wait: int,
) -> None:
    with closing(sqlite3.connect(db_path, timeout=30)) as connection:
        posture.restore_state(connection, original)
    set_status(smoke, headers, 92, original_status)
    time.sleep(cache_wait)
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        posture.verify_targets(connection)
        restored = posture.read_state(connection)
    if current_status(db_path, 92) != original_status:
        raise RuntimeError("ch92 status rollback readback failed")
    if restored != original:
        raise RuntimeError("restored channel/ability state differs from baseline")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="run two forced management probes per channel without mutation",
    )
    parser.add_argument("--cache-wait", type=int, default=75)
    args = parser.parse_args()
    if args.cache_wait < 0:
        parser.error("--cache-wait must not be negative")

    smoke = load_module("sol_failover_smoke", "newapi-local-smoke.py")
    posture = load_module("sol_failover_posture", "update_zzzcoding_sol_primary.py")
    verifier = load_module("sol_failover_verifier", "verify_zzzcoding_sol_primary.py")
    db_path = Path(smoke.NEWAPI_DB).resolve()
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        original = posture.read_state(connection)
        if args.apply:
            posture.verify_targets(connection)
    original_status = int(original["channels"][92][1])
    if original_status != 1:
        raise RuntimeError(f"ch92 must be enabled before drill, got status={original_status}")

    status, _ = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/status", timeout=8)
    if status != 200:
        raise RuntimeError(f"NewAPI baseline failed: HTTP {status}")
    print(
        "baseline: NewAPI=200, "
        f"ch92={original['channels'][92][2]}/{original['channels'][92][3]} "
        f"status={original['channels'][92][1]}, "
        f"ch91={original['channels'][91][2]}/{original['channels'][91][3]}, "
        f"ch83={original['channels'][83][2]}/{original['channels'][83][3]} "
        f"status={original['channels'][83][1]}"
    )
    if not args.apply:
        if args.preflight:
            token, user_id = smoke.admin_auth()
            headers = {
                "Authorization": f"Bearer {token}",
                "New-Api-User": str(user_id),
            }
            option_status, option_body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/option/", headers=headers
            )
            option_violations = smoke.option_policy_violations(
                option_body.get("data") if isinstance(option_body, dict) else None
            )
            if option_status != 200 or option_violations:
                raise RuntimeError(f"NewAPI option policy drift: {option_violations}")
            for channel_id in (92, 91):
                for _ in range(2):
                    posture.management_probe(smoke, headers, channel_id)
            print("preflight: ch92 and ch91 forced management probes 2/2 each; options=ok")
        print("dry-run: no channel status or posture changed")
        return 0

    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    option_status, option_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", headers=headers
    )
    option_violations = smoke.option_policy_violations(
        option_body.get("data") if isinstance(option_body, dict) else None
    )
    if option_status != 200 or "RetryTimes=1" in option_violations or option_violations:
        raise RuntimeError(f"NewAPI option policy drift: {option_violations}")

    for channel_id in (92, 91):
        for _ in range(2):
            posture.management_probe(smoke, headers, channel_id)

    backup = posture.online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    key = verifier.read_client_key(smoke)
    mutation_started = False
    drill_error: BaseException | None = None
    failover_evidence = None
    try:
        set_status(smoke, headers, 92, 2)
        mutation_started = True
        time.sleep(args.cache_wait)
        if current_status(db_path, 92) != 2:
            raise RuntimeError("ch92 disable readback failed")
        with closing(
            sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
        ) as connection:
            last_id = int(connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0])
        failover_evidence = require_probe(
            verifier, smoke, key, FAILOVER_TEXT, 91, last_id
        )
    except BaseException as error:
        drill_error = error
    finally:
        if mutation_started:
            rollback_errors: list[str] = []
            try:
                restore_baseline(
                    posture,
                    smoke,
                    headers,
                    db_path,
                    original,
                    original_status,
                    args.cache_wait,
                )
            except BaseException as rollback_error:
                rollback_errors.append(type(rollback_error).__name__)
            if rollback_errors:
                raise RuntimeError(
                    f"drill rollback failed ({','.join(rollback_errors)}); backup={backup.name}"
                ) from drill_error
    if drill_error is not None:
        raise drill_error

    for _ in range(2):
        posture.management_probe(smoke, headers, 92)
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        last_id = int(connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0])
    recovery_metrics, recovery_log = require_probe(
        verifier, smoke, key, RECOVERY_TEXT, 92, last_id
    )
    failover_metrics, failover_log = failover_evidence
    print(
        f"failover ok: log_id={failover_log[0]} channel_id=91 "
        f"ttft={failover_metrics.ttft_ms}ms total={failover_metrics.total_ms}ms"
    )
    print(
        f"recovery ok: log_id={recovery_log[0]} channel_id=92 "
        f"ttft={recovery_metrics.ttft_ms}ms total={recovery_metrics.total_ms}ms "
        f"backup={backup.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
