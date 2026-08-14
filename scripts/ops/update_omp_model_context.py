"""Back up and update one OMP custom model context window."""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


OFFICIAL_OPUS5_CONTEXT_WINDOW = 200_000


def context_window_line(
    lines: list[str], provider: str, model_id: str
) -> tuple[int, int]:
    active_provider: str | None = None
    active_model: str | None = None
    matches: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2 and stripped.endswith(":"):
            active_provider = stripped[:-1]
            active_model = None
        elif indent == 4 and stripped.startswith("- id:"):
            active_model = stripped.split(":", 1)[1].strip().strip("\"'")
        elif (
            active_provider == provider
            and active_model == model_id
            and indent == 6
            and stripped.startswith("contextWindow:")
        ):
            raw = stripped.split(":", 1)[1].strip()
            try:
                matches.append((index, int(raw)))
            except ValueError as e:
                raise RuntimeError(f"invalid contextWindow value for {provider}/{model_id}") from e
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one contextWindow for {provider}/{model_id}, found {len(matches)}"
        )
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--value", type=int, default=OFFICIAL_OPUS5_CONTEXT_WINDOW
    )
    args = parser.parse_args()
    if args.value <= 0:
        parser.error("--value must be positive")

    path = Path.home() / ".omp" / "agent" / "models.yml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    index, current = context_window_line(
        lines, "zg-newapi-anthropic", "claude-opus-5"
    )
    print(f"current={current} proposed={args.value}")
    if current == args.value:
        print("already configured")
        return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = path.with_name(
        f"models.yml.{time.strftime('%Y%m%d-%H%M%S')}-opus5-context.bak"
    )
    backup.write_bytes(path.read_bytes())
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    lines[index] = f"      contextWindow: {args.value}{newline}"
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text("".join(lines), encoding="utf-8", newline="")
    os.replace(temp, path)
    verify_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    _, verified = context_window_line(
        verify_lines, "zg-newapi-anthropic", "claude-opus-5"
    )
    print(f"backup={backup.name} verified={verified}")
    return 0 if verified == args.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
