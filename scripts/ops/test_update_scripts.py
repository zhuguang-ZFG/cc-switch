"""Unit tests for bounded NewAPI/OMP update helpers."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


OPS_DIR = Path(__file__).parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, OPS_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = load("newapi_local_smoke_for_updates", "newapi-local-smoke.py")
affinity = load("update_newapi_affinity_for_tests", "update_newapi_affinity.py")
omp_context = load("update_omp_model_context_for_tests", "update_omp_model_context.py")


class AffinityUpdateTests(unittest.TestCase):
    def test_rule_updates_satisfy_smoke_contract(self) -> None:
        rules = [
            {"name": name, "model_regex": patterns}
            for name, patterns in affinity.RULE_UPDATES.items()
        ]
        options = [
            {
                "key": "channel_affinity_setting.rules",
                "value": json.dumps(rules),
            }
        ]

        self.assertEqual(smoke.affinity_rule_violations(options), [])


class OmpContextUpdateTests(unittest.TestCase):
    def test_default_matches_official_opus5_context_window(self) -> None:
        self.assertEqual(omp_context.OFFICIAL_OPUS5_CONTEXT_WINDOW, 200_000)

    def test_finds_only_the_requested_provider_model(self) -> None:
        lines = (
            "providers:\n"
            "  zg-newapi:\n"
            "    models:\n"
            "    - id: claude-opus-5\n"
            "      contextWindow: 200000\n"
            "  zg-newapi-anthropic:\n"
            "    models:\n"
            "    - id: claude-opus-5\n"
            "      contextWindow: 110000\n"
        ).splitlines(keepends=True)

        self.assertEqual(
            omp_context.context_window_line(
                lines, "zg-newapi-anthropic", "claude-opus-5"
            ),
            (8, 110000),
        )

    def test_missing_or_duplicate_target_is_rejected(self) -> None:
        missing = ["providers:\n", "  zg-newapi-anthropic:\n"]
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            omp_context.context_window_line(
                missing, "zg-newapi-anthropic", "claude-opus-5"
            )

        duplicate = (
            "providers:\n"
            "  zg-newapi-anthropic:\n"
            "    - id: claude-opus-5\n"
            "      contextWindow: 110000\n"
            "      contextWindow: 110000\n"
        ).splitlines(keepends=True)
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            omp_context.context_window_line(
                duplicate, "zg-newapi-anthropic", "claude-opus-5"
            )


if __name__ == "__main__":
    unittest.main()
