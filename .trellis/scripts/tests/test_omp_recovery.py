"""Deterministic unit tests for the OMP recovery bridge (checkpoint/resume).

Run from anywhere:

    python -m unittest .trellis/scripts/tests/test_omp_recovery
    python -m unittest discover -s .trellis/scripts/tests

Tests use only temporary directories and mocks; they never touch the real
repo, git, or task.json files.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

# Make .trellis/scripts importable regardless of the CWD the suite runs from.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from common import omp_recovery  # noqa: E402
from common.omp_recovery import (  # noqa: E402
    META_KEY,
    SCHEMA_VERSION,
    OmpRecovery,
    VerificationItem,
    build_recovery,
    capture_git_head,
    now_utc_iso,
    parse_verification,
    read_recovery,
    save_recovery,
    validate_recovery,
)
from task import cmd_checkpoint, cmd_resume  # noqa: E402

HEAD_A = "a" * 40
HEAD_B = "b" * 40

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class _RecoveryTestCase(unittest.TestCase):
    """Shared fixture: a temp repo root with one task dir and task.json."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)
        self.task_dir = self.repo_root / "task-a"
        self.task_dir.mkdir()
        self.task_json = self.task_dir / "task.json"
        self._write_task_json(
            {
                "id": "task-a",
                "name": "Task A",
                "status": "in_progress",
                "extraField": {"keep": True},
                "meta": {"custom": "keep-me"},
            }
        )
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_task_json(self, data: dict) -> None:
        self.task_json.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    def _checkpoint_args(self, **overrides) -> argparse.Namespace:
        args = argparse.Namespace(
            dir=str(self.task_dir),
            scope=["cli"],
            completed=["auth flow"],
            pending=["tests"],
            verification=["pytest tests/test_auth.py=PASS"],
            phase="implement",
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def _resume_args(self, **overrides) -> argparse.Namespace:
        args = argparse.Namespace(dir=str(self.task_dir))
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def _run_checkpoint(
        self, args: argparse.Namespace | None = None, head: str | None = HEAD_A
    ) -> int:
        args = args or self._checkpoint_args()
        with (
            patch("task.get_repo_root", return_value=self.repo_root),
            patch("task.capture_git_head", return_value=head),
            redirect_stdout(self.stdout),
            redirect_stderr(self.stderr),
        ):
            return cmd_checkpoint(args)

    def _run_resume(
        self, args: argparse.Namespace | None = None, head: str | None = HEAD_A
    ) -> int:
        args = args or self._resume_args()
        with (
            patch("task.get_repo_root", return_value=self.repo_root),
            patch("task.capture_git_head", return_value=head),
            redirect_stdout(self.stdout),
            redirect_stderr(self.stderr),
        ):
            return cmd_resume(args)


# =============================================================================
# parse_verification
# =============================================================================

class ParseVerificationTest(unittest.TestCase):
    def test_valid_command_result(self) -> None:
        item = parse_verification("pytest tests/test_auth.py=PASS")
        self.assertEqual(item.command, "pytest tests/test_auth.py")
        self.assertEqual(item.result, "PASS")

    def test_strips_surrounding_whitespace(self) -> None:
        item = parse_verification("  task.py validate .  =  OK  ")
        self.assertEqual(item.command, "task.py validate .")
        self.assertEqual(item.result, "OK")

    def test_splits_on_first_equals(self) -> None:
        item = parse_verification("run --flag=a=b")
        self.assertEqual(item.command, "run --flag")
        self.assertEqual(item.result, "a=b")

    def test_invalid_inputs_raise_value_error(self) -> None:
        for bad in ("noequals", "=result", "command=", "", "   "):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_verification(bad)

    def test_non_string_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_verification(123)  # type: ignore[arg-type]


# =============================================================================
# build_recovery / validate_recovery
# =============================================================================

class BuildAndValidateTest(unittest.TestCase):
    def test_build_round_trips_through_validator(self) -> None:
        payload = build_recovery(
            phase="implement",
            scope=["cli", "backend"],
            completed=["auth flow"],
            pending=["tests"],
            verification=[VerificationItem("pytest", "PASS")],
            git_head=HEAD_A,
            updated_at="2026-08-06T12:34:56Z",
        )
        recovery = validate_recovery(payload)
        self.assertIsInstance(recovery, OmpRecovery)
        self.assertEqual(recovery.schema_version, SCHEMA_VERSION)
        self.assertEqual(recovery.phase, "implement")
        self.assertEqual(recovery.scope, ("cli", "backend"))
        self.assertEqual(recovery.completed, ("auth flow",))
        self.assertEqual(recovery.pending, ("tests",))
        self.assertEqual(recovery.verification, (VerificationItem("pytest", "PASS"),))
        self.assertEqual(recovery.git_head, HEAD_A)
        self.assertEqual(recovery.updated_at, "2026-08-06T12:34:56Z")
        self.assertEqual(recovery.to_dict(), payload)

    def test_build_defaults(self) -> None:
        payload = build_recovery()
        self.assertEqual(payload["phase"], "")
        self.assertEqual(payload["scope"], [])
        self.assertEqual(payload["completed"], [])
        self.assertEqual(payload["pending"], [])
        self.assertEqual(payload["verification"], [])
        self.assertIsNone(payload["git_head"])
        self.assertRegex(payload["updated_at"], _ISO_UTC_RE)
        # Defaults must still be a valid snapshot.
        validate_recovery(payload)

    def test_updated_at_defaults_to_utc_iso(self) -> None:
        self.assertRegex(now_utc_iso(), _ISO_UTC_RE)
        self.assertTrue(now_utc_iso().endswith("Z"))

    def test_build_invalid_inputs_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            build_recovery(phase=42)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            build_recovery(scope=["ok", 42])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            build_recovery(verification=["not an item"])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            build_recovery(verification=[VerificationItem("cmd", "")])
        with self.assertRaises(ValueError):
            build_recovery(updated_at="not-a-timestamp")

    def test_validate_rejection_cases(self) -> None:
        base = build_recovery(updated_at="2026-08-06T12:34:56Z")
        cases = [
            ("wrong schema version", {**base, "schema_version": 99}),
            ("missing phase", {k: v for k, v in base.items() if k != "phase"}),
            ("phase not a string", {**base, "phase": 1}),
            ("scope not a list", {**base, "scope": "cli"}),
            ("scope with empty entry", {**base, "scope": ["ok", "  "]}),
            ("verification not a list", {**base, "verification": {}}),
            (
                "verification entry missing result",
                {**base, "verification": [{"command": "cmd"}]},
            ),
            ("git_head empty string", {**base, "git_head": ""}),
            ("git_head not str", {**base, "git_head": 5}),
            ("updated_at missing", {k: v for k, v in base.items() if k != "updated_at"}),
            ("updated_at bad format", {**base, "updated_at": "06/08/2026"}),
        ]
        for label, bad in cases:
            with self.assertRaises(ValueError, msg=label):
                validate_recovery(bad)

    def test_validate_rejects_non_dict(self) -> None:
        with self.assertRaises(ValueError):
            validate_recovery(["not", "a", "dict"])


# =============================================================================
# save_recovery (atomic write invocation + field preservation)
# =============================================================================

class SaveRecoveryTest(_RecoveryTestCase):
    def test_save_invokes_atomic_write_json_once(self) -> None:
        recovery = build_recovery(
            phase="implement",
            scope=["cli"],
            verification=[VerificationItem("pytest", "PASS")],
            git_head=HEAD_A,
            updated_at="2026-08-06T12:34:56Z",
        )
        with patch("common.omp_recovery.write_json", return_value=True) as mock_write:
            result = save_recovery(self.task_json, recovery)
        self.assertTrue(result)
        mock_write.assert_called_once()
        written_path, written_data = mock_write.call_args.args
        self.assertEqual(written_path, self.task_json)
        # Snapshot stored under meta.omp_recovery, untouched fields preserved.
        self.assertEqual(written_data["meta"][META_KEY], recovery)
        self.assertEqual(written_data["id"], "task-a")
        self.assertEqual(written_data["status"], "in_progress")
        self.assertEqual(written_data["extraField"], {"keep": True})
        self.assertEqual(written_data["meta"]["custom"], "keep-me")

    def test_save_creates_meta_when_absent(self) -> None:
        self._write_task_json({"id": "bare", "status": "planning"})
        recovery = build_recovery(updated_at="2026-08-06T12:34:56Z")
        with patch("common.omp_recovery.write_json", return_value=True) as mock_write:
            save_recovery(self.task_json, recovery)
        written_data = mock_write.call_args.args[1]
        self.assertEqual(written_data["meta"][META_KEY], recovery)
        self.assertEqual(written_data["id"], "bare")

    def test_save_missing_file_raises(self) -> None:
        missing = self.task_dir / "absent.json"
        with self.assertRaises(FileNotFoundError):
            save_recovery(missing, build_recovery())

    def test_save_invalid_json_raises(self) -> None:
        self.task_json.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            save_recovery(self.task_json, build_recovery())

    def test_save_invalid_recovery_raises(self) -> None:
        with self.assertRaises(ValueError):
            save_recovery(self.task_json, {"schema_version": 99})

    def test_save_non_dict_meta_refused_and_file_untouched(self) -> None:
        self._write_task_json({"id": "x", "meta": "not-an-object"})
        with self.assertRaises(ValueError):
            save_recovery(self.task_json, build_recovery())
        data = json.loads(self.task_json.read_text(encoding="utf-8"))
        self.assertEqual(data["meta"], "not-an-object")


# =============================================================================
# checkpoint command
# =============================================================================

class CheckpointCommandTest(_RecoveryTestCase):
    def test_checkpoint_preserves_unknown_fields(self) -> None:
        self.assertEqual(self._run_checkpoint(), 0)
        data = json.loads(self.task_json.read_text(encoding="utf-8"))
        # Original fields survive exactly.
        self.assertEqual(data["id"], "task-a")
        self.assertEqual(data["name"], "Task A")
        self.assertEqual(data["status"], "in_progress")
        self.assertEqual(data["extraField"], {"keep": True})
        self.assertEqual(data["meta"]["custom"], "keep-me")
        # Snapshot is present and well-formed.
        snapshot = data["meta"][META_KEY]
        self.assertEqual(snapshot["schema_version"], SCHEMA_VERSION)
        self.assertEqual(snapshot["phase"], "implement")
        self.assertEqual(snapshot["scope"], ["cli"])
        self.assertEqual(snapshot["completed"], ["auth flow"])
        self.assertEqual(snapshot["pending"], ["tests"])
        self.assertEqual(
            snapshot["verification"],
            [{"command": "pytest tests/test_auth.py", "result": "PASS"}],
        )
        self.assertEqual(snapshot["git_head"], HEAD_A)
        self.assertRegex(snapshot["updated_at"], _ISO_UTC_RE)

    def test_checkpoint_replaces_snapshot_not_merges(self) -> None:
        self._run_checkpoint()
        second = self._checkpoint_args(
            scope=["cli", "backend"],
            completed=["auth flow", "more"],
            pending=["e2e"],
            verification=["pytest=FAIL", "build=OK"],
            phase="review",
        )
        self.assertEqual(self._run_checkpoint(second, head=HEAD_B), 0)
        data = json.loads(self.task_json.read_text(encoding="utf-8"))
        snapshot = data["meta"][META_KEY]
        self.assertEqual(snapshot["phase"], "review")
        self.assertEqual(snapshot["scope"], ["cli", "backend"])
        self.assertEqual(snapshot["completed"], ["auth flow", "more"])
        self.assertEqual(snapshot["pending"], ["e2e"])
        self.assertEqual(
            snapshot["verification"],
            [{"command": "pytest", "result": "FAIL"}, {"command": "build", "result": "OK"}],
        )
        self.assertEqual(snapshot["git_head"], HEAD_B)
        # Unknown fields still preserved across replacement writes.
        data = json.loads(self.task_json.read_text(encoding="utf-8"))
        self.assertEqual(data["extraField"], {"keep": True})
        self.assertEqual(data["meta"]["custom"], "keep-me")

    def test_checkpoint_rerun_with_fewer_lists_drops_previous(self) -> None:
        self._run_checkpoint()
        second = self._checkpoint_args(scope=["only"], completed=[], pending=[], verification=[])
        self.assertEqual(self._run_checkpoint(second), 0)
        snapshot = json.loads(self.task_json.read_text(encoding="utf-8"))["meta"][META_KEY]
        self.assertEqual(snapshot["scope"], ["only"])
        self.assertEqual(snapshot["completed"], [])
        self.assertEqual(snapshot["pending"], [])
        self.assertEqual(snapshot["verification"], [])

    def test_checkpoint_invalid_verification_rejected(self) -> None:
        args = self._checkpoint_args(verification=["pytest PASS"])  # missing '='
        self.assertEqual(self._run_checkpoint(args), 1)
        self.assertIn("invalid verification", self.stderr.getvalue())
        # Nothing was written.
        data = json.loads(self.task_json.read_text(encoding="utf-8"))
        self.assertNotIn(META_KEY, data.get("meta", {}))

    def test_checkpoint_missing_task_dir(self) -> None:
        args = self._checkpoint_args()
        args.dir = str(self.repo_root / "no-such-task")
        self.assertEqual(self._run_checkpoint(args), 1)
        self.assertIn("Task not found", self.stderr.getvalue())

    def test_checkpoint_missing_task_json(self) -> None:
        (self.task_json).unlink()
        self.assertEqual(self._run_checkpoint(), 1)
        self.assertIn("task.json not found", self.stderr.getvalue())


# =============================================================================
# resume command
# =============================================================================

class ResumeCommandTest(_RecoveryTestCase):
    def _checkpointed(self, head: str | None = HEAD_A) -> None:
        self.assertEqual(self._run_checkpoint(head=head), 0)

    def test_resume_is_read_only(self) -> None:
        self._checkpointed()
        before = self.task_json.read_bytes()
        self.assertEqual(self._run_resume(), 0)
        after = self.task_json.read_bytes()
        self.assertEqual(after, before)

    def test_resume_shows_all_sections(self) -> None:
        self._checkpointed()
        self.assertEqual(self._run_resume(), 0)
        out = self.stdout.getvalue()
        self.assertIn("OMP Recovery Checkpoint", out)
        self.assertIn(f"Task: {self.task_json}", out)
        self.assertIn("Phase: implement", out)
        self.assertRegex(out, r"Updated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
        self.assertIn("Scope:", out)
        self.assertIn("  - cli", out)
        self.assertIn("Completed:", out)
        self.assertIn("  - auth flow", out)
        self.assertIn("Pending:", out)
        self.assertIn("  - tests", out)
        self.assertIn("Verification:", out)
        self.assertIn("  - pytest tests/test_auth.py => PASS", out)
        self.assertIn(f"Checkpoint HEAD: {HEAD_A}", out)
        self.assertIn(f"Current HEAD:    {HEAD_A}", out)
        self.assertNotIn("Warning:", out)

    def test_resume_drift_warning_when_head_differs(self) -> None:
        self._checkpointed(head=HEAD_A)
        self.assertEqual(self._run_resume(head=HEAD_B), 0)
        out = self.stdout.getvalue()
        self.assertIn(f"Checkpoint HEAD: {HEAD_A}", out)
        self.assertIn(f"Current HEAD:    {HEAD_B}", out)
        self.assertIn("Warning: git HEAD differs from checkpoint", out)
        self.assertIn(f"({HEAD_A} -> {HEAD_B})", out)

    def test_resume_no_warning_when_head_unchanged(self) -> None:
        self._checkpointed(head=HEAD_A)
        self.assertEqual(self._run_resume(head=HEAD_A), 0)
        self.assertNotIn("Warning:", self.stdout.getvalue())

    def test_resume_note_when_comparison_impossible(self) -> None:
        self._checkpointed(head=None)  # checkpoint recorded no git HEAD
        self.assertEqual(self._run_resume(head=None), 0)
        out = self.stdout.getvalue()
        self.assertIn("Checkpoint HEAD: (unavailable)", out)
        self.assertIn("Current HEAD:    (unavailable)", out)
        self.assertIn("Note: git HEAD comparison not possible", out)
        self.assertNotIn("Warning:", out)

    def test_resume_missing_checkpoint(self) -> None:
        self.assertEqual(self._run_resume(), 1)
        self.assertIn("no OMP recovery checkpoint", self.stderr.getvalue())

    def test_resume_malformed_checkpoint(self) -> None:
        data = {
            "id": "task-a",
            "meta": {META_KEY: {"schema_version": 99, "phase": "x"}},
        }
        self._write_task_json(data)
        self.assertEqual(self._run_resume(), 1)
        self.assertIn("malformed checkpoint", self.stderr.getvalue())
        self.assertIn("schema_version", self.stderr.getvalue())

    def test_resume_missing_task_dir(self) -> None:
        args = self._resume_args()
        args.dir = str(self.repo_root / "no-such-task")
        self.assertEqual(self._run_resume(args), 1)
        self.assertIn("Task not found", self.stderr.getvalue())

    def test_resume_missing_task_json(self) -> None:
        (self.task_json).unlink()
        self.assertEqual(self._run_resume(), 1)
        self.assertIn("task.json not found", self.stderr.getvalue())


# =============================================================================
# read_recovery / capture_git_head
# =============================================================================

class ReadRecoveryTest(_RecoveryTestCase):
    def test_read_absent_returns_none(self) -> None:
        self.assertIsNone(read_recovery(self.task_json))

    def test_read_missing_file_returns_none(self) -> None:
        (self.task_json).unlink()
        self.assertIsNone(read_recovery(self.task_json))

    def test_read_valid_snapshot(self) -> None:
        self._run_checkpoint()
        recovery = read_recovery(self.task_json)
        self.assertIsInstance(recovery, OmpRecovery)
        self.assertEqual(recovery.phase, "implement")
        self.assertEqual(recovery.git_head, HEAD_A)

    def test_read_malformed_raises(self) -> None:
        self._write_task_json(
            {"meta": {META_KEY: {"schema_version": 1, "phase": "x", "scope": "not-a-list"}}}
        )
        with self.assertRaises(ValueError):
            read_recovery(self.task_json)


class CaptureGitHeadTest(unittest.TestCase):
    def test_returns_stripped_sha_on_success(self) -> None:
        with patch("common.omp_recovery.run_git", return_value=(0, f"{HEAD_A}\n", "")) as mock_run:
            head = capture_git_head(Path("."))
        self.assertEqual(head, HEAD_A)
        mock_run.assert_called_once_with(["rev-parse", "HEAD"], cwd=Path("."))

    def test_returns_none_on_failure(self) -> None:
        for failure in (
            (1, "", "fatal: not a git repository"),
            (0, "", ""),
            (0, "\n", ""),
        ):
            with patch("common.omp_recovery.run_git", return_value=failure):
                self.assertIsNone(capture_git_head(Path(".")))


if __name__ == "__main__":
    unittest.main()
