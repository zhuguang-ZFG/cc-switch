"""Hermetic gate invariants for ops scripts (no network, no live DB).

Covers the two files the 2026-10-15 follow-up changed and that no other project
check selected: verify_omp_newapi_alignment.py (the QUARANTINE table) and
sync_zen_free_bridge_models_20261015.py (ratio merge + target policy + probe
verdicts).

The duplicate-key check is a real regression guard, not a style rule: QUARANTINE
is a plain python dict literal, so a repeated key silently shadows the earlier
line -- exactly what a stale `gemini-3.8-flash` duplicate did until 2026-10-15
(the newer evidence line sat above an older duplicate and never printed).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

OPS_DIR = Path(__file__).parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, OPS_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SYNC = load("zen_bridge_sync_for_tests", "sync_zen_free_bridge_models_20261015.py")


class QuarantineTableTest(unittest.TestCase):
    def test_quarantine_keys_unique_and_reasons_nonempty(self) -> None:
        source = (OPS_DIR / "verify_omp_newapi_alignment.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "QUARANTINE" for t in node.targets)):
                continue
            table = node.value
            self.assertIsInstance(table, ast.Dict)
            assert isinstance(table, ast.Dict)  # narrow for type checkers
            keys = [key.value for key in table.keys]
            self.assertTrue(keys, "QUARANTINE table is empty")
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            self.assertEqual(duplicates, [],
                             f"duplicate QUARANTINE keys shadow earlier evidence: {duplicates}")
            for key, reason in zip(keys, table.values):
                self.assertTrue(isinstance(key, str) and key.strip(),
                                "QUARANTINE key must be a non-empty string")
                self.assertTrue(
                    isinstance(reason, ast.Constant) and isinstance(reason.value, str)
                    and reason.value.strip(),
                    f"QUARANTINE[{key}] reason must be a non-empty string literal",
                )
            return
        self.fail("QUARANTINE assignment not found in verify_omp_newapi_alignment.py")


class MergeRatioTest(unittest.TestCase):
    def test_adds_at_free_ratio_and_removes(self) -> None:
        current = json.dumps({"alpha": 1, "beta": 0.5}, sort_keys=True)

        text = SYNC.merge_ratio(current, ("beta",), ("gamma",))

        self.assertEqual(json.loads(text), {"alpha": 1, "gamma": SYNC.FREE_MODEL_RATIO})
        self.assertEqual(text, json.dumps({"alpha": 1, "gamma": SYNC.FREE_MODEL_RATIO},
                                          separators=(",", ":"), sort_keys=True))

    def test_removing_absent_key_is_a_noop(self) -> None:
        current = json.dumps({"alpha": 1})
        self.assertEqual(json.loads(SYNC.merge_ratio(current, ("missing",), ())), {"alpha": 1})

    def test_invalid_or_non_object_json_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            SYNC.merge_ratio("[]", (), ())
        with self.assertRaises(RuntimeError):
            SYNC.merge_ratio("{", (), ())


class ComputeTargetTest(unittest.TestCase):
    CURRENT = ["a", "b"]
    CANDIDATES = ["a", "b", "c"]

    @staticmethod
    def results(**live: bool) -> dict[str, dict]:
        return {model: {"live": value} for model, value in live.items()}

    def test_additive_keeps_current_even_when_probing_failed(self) -> None:
        target, added, removed = SYNC.compute_target(
            self.CANDIDATES, self.CURRENT, self.results(a=True, b=False, c=True),
            allow_drop=False)
        self.assertEqual(target, ["a", "b", "c"])
        self.assertEqual(added, ("c",))
        self.assertEqual(removed, ())

    def test_allow_drop_removes_dead_current(self) -> None:
        target, added, removed = SYNC.compute_target(
            self.CANDIDATES, self.CURRENT, self.results(a=True, b=False, c=True),
            allow_drop=True)
        self.assertEqual(target, ["a", "c"])
        self.assertEqual(added, ("c",))
        self.assertEqual(removed, ("b",))

    def test_grace_live_current_is_kept_in_both_modes(self) -> None:
        for allow_drop in (False, True):
            target, added, removed = SYNC.compute_target(
                self.CANDIDATES, self.CURRENT, self.results(a=True, b=True, c=False),
                allow_drop=allow_drop)
            self.assertEqual(target, ["a", "b"])
            self.assertEqual(added, ())
            self.assertEqual(removed, ())


class _FakeSmoke:
    def __init__(self, replies: list[tuple[int, dict]]):
        self.replies = list(replies)
        self.calls = 0

    def http_json(self, url, *, method="GET", body=None, headers=None, timeout=15):
        self.calls += 1
        return self.replies.pop(0)


class ProbeVerdictTest(unittest.TestCase):
    @staticmethod
    def _completion(text: str) -> tuple[int, dict]:
        return 200, {"choices": [{"message": {"content": text}}]}

    def test_live_requires_200_with_text(self) -> None:
        self.assertTrue(SYNC._probe_once(_FakeSmoke([self._completion("OK")]), "m")["live"])

    def test_200_with_empty_content_is_not_live(self) -> None:
        self.assertFalse(SYNC._probe_once(_FakeSmoke([self._completion("")]), "m")["live"])

    def test_quota_and_busy_are_classified_separately(self) -> None:
        quota = SYNC._probe_once(_FakeSmoke([(429, {"error": "limit"})]), "m")
        busy = SYNC._probe_once(_FakeSmoke([(503, {"error": "busy"})]), "m")
        self.assertTrue(quota["quota"])
        self.assertFalse(quota["live"])
        self.assertEqual(busy["status"], 503)

    def test_probe_retries_until_live(self) -> None:
        smoke = _FakeSmoke([(502, {"error": "flake"}), self._completion("OK")])
        with mock.patch.object(SYNC.time, "sleep"):
            result = SYNC.probe_bridge(smoke, "m")
        self.assertTrue(result["live"])
        self.assertEqual(result["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
