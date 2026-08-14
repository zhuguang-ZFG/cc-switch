"""Back up and bound OMP's automatic AnyRouter route and proxy timeout."""
from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path


OMP_CONFIG = Path.home() / ".omp" / "agent" / "config.yml"
ANYROUTER_PROXY = (
    Path.home() / ".kimi-code" / "proxies" / "anyrouter-proxy" / "proxy.cjs"
)
ANYROUTER_SELECTOR = "anyrouter/claude-opus-5"


def read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def transform_omp_config(text: str) -> str:
    lines = text.splitlines(keepends=True)
    in_fallbacks = False
    active_chain: str | None = None
    matches: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2 and stripped == "fallbackChains:":
            in_fallbacks = True
            active_chain = None
            continue
        if in_fallbacks and indent <= 2 and stripped:
            in_fallbacks = False
            active_chain = None
        if in_fallbacks and indent == 4 and stripped.endswith(":"):
            active_chain = stripped[:-1]
            continue
        if (
            in_fallbacks
            and active_chain == "slow"
            and indent == 6
            and stripped == f"- {ANYROUTER_SELECTOR}"
        ):
            matches.append(index)
    if len(matches) > 1:
        raise RuntimeError(f"expected at most one AnyRouter slow entry, found {len(matches)}")
    if matches:
        del lines[matches[0]]
    return "".join(lines)


def transform_proxy(text: str) -> str:
    timeout_old = "timeout: 600000,"
    timeout_new = "timeout: 180000,"
    header_old = "'x-stainless-timeout': '600',"
    header_new = "'x-stainless-timeout': '180',"
    old_timeout_count = text.count(timeout_old)
    new_timeout_count = text.count(timeout_new)
    old_header_count = text.count(header_old)
    new_header_count = text.count(header_new)
    if (old_timeout_count, new_timeout_count) not in ((2, 0), (0, 2)):
        raise RuntimeError(
            "expected exactly two 600s or two 180s proxy timeouts, found "
            f"600s={old_timeout_count} 180s={new_timeout_count}"
        )
    if (old_header_count, new_header_count) not in ((1, 0), (0, 1)):
        raise RuntimeError(
            "expected exactly one 600s or one 180s timeout header, found "
            f"600s={old_header_count} 180s={new_header_count}"
        )
    transformed = text.replace(timeout_old, timeout_new).replace(
        header_old, header_new
    )
    if transformed.count(timeout_new) != 2 or transformed.count(header_new) != 1:
        raise RuntimeError("proxy timeout transformation did not reach the target posture")
    return transformed


def write_atomic(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    omp_text = read_text_exact(OMP_CONFIG)
    proxy_text = read_text_exact(ANYROUTER_PROXY)
    next_omp = transform_omp_config(omp_text)
    next_proxy = transform_proxy(proxy_text)
    print(
        f"omp_anyrouter_slow_entries={omp_text.count('- ' + ANYROUTER_SELECTOR)}"
        f"->{next_omp.count('- ' + ANYROUTER_SELECTOR)}"
    )
    print(
        f"proxy_timeout_600s={proxy_text.count('timeout: 600000,')}"
        f"->{next_proxy.count('timeout: 600000,')}"
    )
    print(
        f"proxy_timeout_180s={proxy_text.count('timeout: 180000,')}"
        f"->{next_proxy.count('timeout: 180000,')}"
    )
    if omp_text == next_omp and proxy_text == next_proxy:
        print("already configured")
        return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    omp_backup = OMP_CONFIG.with_name(f"config.yml.{stamp}-anyrouter-timeout.bak")
    proxy_backup = ANYROUTER_PROXY.with_name(
        f"proxy.cjs.{stamp}-timeout600s.bak"
    )
    shutil.copy2(OMP_CONFIG, omp_backup)
    shutil.copy2(ANYROUTER_PROXY, proxy_backup)
    try:
        write_atomic(OMP_CONFIG, next_omp)
        write_atomic(ANYROUTER_PROXY, next_proxy)
        verified = (
            read_text_exact(OMP_CONFIG) == next_omp
            and read_text_exact(ANYROUTER_PROXY) == next_proxy
        )
        if not verified:
            raise RuntimeError("post-write verification failed")
    except Exception:
        shutil.copy2(omp_backup, OMP_CONFIG)
        shutil.copy2(proxy_backup, ANYROUTER_PROXY)
        raise
    print(f"omp_backup={omp_backup.name}")
    print(f"proxy_backup={proxy_backup.name}")
    print("verified=True; recycle only the AnyRouter proxy process")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
