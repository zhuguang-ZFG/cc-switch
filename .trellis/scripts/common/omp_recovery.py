"""
OMP recovery bridge: typed read/write of ``meta.omp_recovery`` in task.json.

Single source of truth for the recovery snapshot contract:

    schema_version = 1
    phase          -> str (may be empty when --phase is omitted)
    scope          -> list[str] of non-empty strings
    completed      -> list[str] of non-empty strings
    pending        -> list[str] of non-empty strings
    verification   -> list of {"command": str, "result": str} objects
    git_head       -> str | null (best-effort git rev-parse HEAD)
    updated_at     -> UTC ISO-8601 string (e.g. 2026-08-06T12:34:56Z)

Construction and reading go through the same validator, so a snapshot
written by ``task.py checkpoint`` is guaranteed to round-trip through
``task.py resume`` without reinterpretation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .git import run_git
from .io import read_json, write_json

#: Key under task.json "meta" that holds the recovery snapshot.
META_KEY = "omp_recovery"

#: Contract version for meta.omp_recovery.schema_version.
SCHEMA_VERSION = 1

#: Accepts e.g. 2026-08-06T12:34:56Z or 2026-08-06T12:34:56.123+00:00.
_ISO_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True)
class VerificationItem:
    """One recorded verification: a command and its observed result."""

    command: str
    result: str


@dataclass(frozen=True)
class OmpRecovery:
    """Typed view of a validated ``meta.omp_recovery`` snapshot.

    Immutable so a resume display can never be mutated accidentally;
    ``to_dict`` produces the JSON payload for writing.
    """

    schema_version: int
    phase: str
    scope: tuple[str, ...]
    completed: tuple[str, ...]
    pending: tuple[str, ...]
    verification: tuple[VerificationItem, ...]
    git_head: str | None
    updated_at: str

    def to_dict(self) -> dict:
        """Serialize back to the plain-dict snapshot shape."""
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "scope": list(self.scope),
            "completed": list(self.completed),
            "pending": list(self.pending),
            "verification": [
                {"command": item.command, "result": item.result}
                for item in self.verification
            ],
            "git_head": self.git_head,
            "updated_at": self.updated_at,
        }


def now_utc_iso() -> str:
    """Current UTC time as an ISO-8601 string, e.g. ``2026-08-06T12:34:56Z``."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_verification(raw: str) -> VerificationItem:
    """Parse one ``COMMAND=RESULT`` CLI argument into a VerificationItem.

    Raises ValueError when the argument is malformed: no ``=`` separator,
    an empty command, or an empty result.
    """
    if not isinstance(raw, str):
        raise ValueError(
            f"invalid verification {raw!r}: expected COMMAND=RESULT"
        )
    command, sep, result = raw.partition("=")
    command = command.strip()
    result = result.strip()
    if not sep or not command:
        raise ValueError(
            f"invalid verification {raw!r}: expected COMMAND=RESULT (empty command)"
        )
    if not result:
        raise ValueError(
            f"invalid verification {raw!r}: expected COMMAND=RESULT (empty result)"
        )
    return VerificationItem(command=command, result=result)


def _require_str_list(value: object, name: str) -> list[str]:
    """Validate that a snapshot field is a list of non-empty strings."""
    if not isinstance(value, list):
        raise ValueError(f"meta.omp_recovery.{name} must be a list")
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f"meta.omp_recovery.{name} entries must be non-empty strings"
            )
        items.append(entry.strip())
    return items


def _parse_verification_list(value: object) -> list[VerificationItem]:
    """Validate the verification field into a list of VerificationItem."""
    if not isinstance(value, list):
        raise ValueError("meta.omp_recovery.verification must be a list")
    items: list[VerificationItem] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError(
                "meta.omp_recovery.verification entries must be objects "
                "with non-empty command and result"
            )
        command = entry.get("command")
        result = entry.get("result")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(
                "meta.omp_recovery.verification entries need a non-empty command"
            )
        if not isinstance(result, str) or not result.strip():
            raise ValueError(
                "meta.omp_recovery.verification entries need a non-empty result"
            )
        items.append(
            VerificationItem(command=command.strip(), result=result.strip())
        )
    return items


def validate_recovery(data: object) -> OmpRecovery:
    """Validate a parsed ``meta.omp_recovery`` dict.

    Returns an immutable :class:`OmpRecovery` on success and raises
    ValueError with a precise message on any malformed field, so callers
    can surface exactly which part of a corrupted checkpoint is wrong.
    """
    if not isinstance(data, dict):
        raise ValueError("meta.omp_recovery must be an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            "unsupported meta.omp_recovery.schema_version: "
            f"{data.get('schema_version')!r} (expected {SCHEMA_VERSION})"
        )
    phase = data.get("phase")
    if not isinstance(phase, str):
        raise ValueError("meta.omp_recovery.phase must be a string")
    git_head = data.get("git_head")
    if git_head is not None and (
        not isinstance(git_head, str) or not git_head.strip()
    ):
        raise ValueError("meta.omp_recovery.git_head must be a string or null")
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, str) or not _ISO_UTC_RE.match(updated_at.strip()):
        raise ValueError(
            f"meta.omp_recovery.updated_at is not UTC ISO-8601: {updated_at!r}"
        )
    return OmpRecovery(
        schema_version=SCHEMA_VERSION,
        phase=phase,
        scope=tuple(_require_str_list(data.get("scope"), "scope")),
        completed=tuple(_require_str_list(data.get("completed"), "completed")),
        pending=tuple(_require_str_list(data.get("pending"), "pending")),
        verification=tuple(_parse_verification_list(data.get("verification"))),
        git_head=git_head.strip() if git_head else None,
        updated_at=updated_at.strip(),
    )


def build_recovery(
    *,
    phase: str = "",
    scope: list[str] | None = None,
    completed: list[str] | None = None,
    pending: list[str] | None = None,
    verification: list[VerificationItem] | None = None,
    git_head: str | None = None,
    updated_at: str | None = None,
) -> dict:
    """Build a validated ``meta.omp_recovery`` snapshot dict.

    ``updated_at`` defaults to the current UTC time. Raises ValueError on
    invalid input using the same rules as :func:`validate_recovery`, so a
    checkpoint can never be persisted in a shape resume cannot read.
    """
    if not isinstance(phase, str):
        raise ValueError("meta.omp_recovery.phase must be a string")
    verification_items = list(verification or [])
    for item in verification_items:
        if not isinstance(item, VerificationItem):
            raise ValueError(
                "verification entries must be VerificationItem instances "
                "(use parse_verification for COMMAND=RESULT strings)"
            )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "scope": list(scope or []),
        "completed": list(completed or []),
        "pending": list(pending or []),
        "verification": [
            {"command": item.command, "result": item.result}
            for item in verification_items
        ],
        "git_head": git_head,
        "updated_at": updated_at or now_utc_iso(),
    }
    # Round-trip through the shared validator so build/read stay in sync.
    validate_recovery(payload)
    return payload


def read_recovery(task_json_path: Path) -> OmpRecovery | None:
    """Read and validate the snapshot from a task.json.

    Returns None when there is no checkpoint (missing file or no
    ``meta.omp_recovery`` key) and raises ValueError when a checkpoint is
    present but malformed.
    """
    data = read_json(task_json_path)
    if data is None:
        return None
    meta = data.get("meta")
    if not isinstance(meta, dict) or META_KEY not in meta:
        return None
    return validate_recovery(meta[META_KEY])


def save_recovery(task_json_path: Path, recovery: dict) -> bool:
    """Persist a validated snapshot into ``meta.omp_recovery``.

    The snapshot is replaced, never merged with the previous one. All
    other task.json fields -- including unknown top-level fields and
    unknown ``meta`` keys -- are preserved exactly. The write goes
    through :func:`common.io.write_json`, which is atomic.

    Raises FileNotFoundError if task.json is absent and ValueError if it
    is not valid JSON or the snapshot fails validation.
    """
    if not task_json_path.is_file():
        raise FileNotFoundError(f"task.json not found: {task_json_path}")
    data = read_json(task_json_path)
    if data is None:
        raise ValueError(f"task.json is not valid JSON: {task_json_path}")
    validate_recovery(recovery)
    meta = data.get("meta")
    if meta is not None and not isinstance(meta, dict):
        raise ValueError(
            f"task.json meta is not an object; refusing to overwrite: {task_json_path}"
        )
    if not isinstance(meta, dict):
        meta = {}
        data["meta"] = meta
    meta[META_KEY] = recovery
    return write_json(task_json_path, data)


def capture_git_head(repo_root: Path) -> str | None:
    """Best-effort current git HEAD (full SHA) without making a commit.

    Returns None when git is unavailable, the repo has no HEAD yet, or the
    command fails for any reason -- recovery must never fail on git.
    """
    returncode, stdout, _stderr = run_git(["rev-parse", "HEAD"], cwd=repo_root)
    if returncode != 0:
        return None
    head = stdout.strip()
    return head or None
