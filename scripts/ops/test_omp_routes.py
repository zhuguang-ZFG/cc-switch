"""OMP 大工程路由静态门禁。"""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


REAL_USER_HOME = Path(os.environ.get("OMP_REAL_HOME", "C:/Users/zhugu"))
CONFIG_FILE = REAL_USER_HOME / ".omp" / "agent" / "config.yml"
CRITICAL_ROLES = ("slow", "plan", "vision")
FORBIDDEN_CRITICAL_CANDIDATES = {
    "agentrouter/claude-opus-5",
    "agentrouter/claude-opus-4-8",
}
ALLOWED_CODEBUDDY_MODELS = {"hy3-preview-agent", "gpt-5.6-sol"}


def _fallback_chain_entries(text: str) -> dict[str, list[str]]:
    """从 config.yml 提取全部 fallback chain；保持依赖为标准库。"""
    result: dict[str, list[str]] = {}
    active_chain: str | None = None
    in_chains = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped == "fallbackChains:":
            in_chains = True
            active_chain = None
            continue
        if not in_chains:
            continue
        if indent <= 2 and stripped and stripped != "fallbackChains:":
            break
        if indent == 4 and stripped.endswith(":"):
            active_chain = stripped[:-1]
            result[active_chain] = []
            continue
        if active_chain and indent == 6 and stripped.startswith("- "):
            result[active_chain].append(stripped[2:].strip())
    return result


class OmpRouteGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CONFIG_FILE.exists():
            raise unittest.SkipTest(f"local OMP config not present: {CONFIG_FILE}")
        if shutil.which("omp") is None:
            raise unittest.SkipTest("omp CLI is not installed")
    def test_model_fallback_is_enabled(self):
        text = CONFIG_FILE.read_text(encoding="utf-8")
        self.assertIn("  modelFallback: true", text)
        self.assertIn("  fallbackRevertPolicy: cooldown-expiry", text)

    def test_critical_chains_exclude_known_bad_agentrouter_claude(self):
        chains = _fallback_chain_entries(CONFIG_FILE.read_text(encoding="utf-8"))
        for role, candidates in chains.items():
            with self.subTest(role=role):
                self.assertTrue(candidates, f"{role} fallback chain must not be empty")
                self.assertTrue(
                    FORBIDDEN_CRITICAL_CANDIDATES.isdisjoint(candidates),
                    f"{role} contains known-sensitive-word AgentRouter Claude route: {candidates}",
                )

    def test_codebuddy_fallbacks_use_only_hy3_and_sol(self):
        chains = _fallback_chain_entries(CONFIG_FILE.read_text(encoding="utf-8"))
        for chain, candidates in chains.items():
            codebuddy_models = {
                candidate.split("/", 1)[1]
                for candidate in candidates
                if candidate.startswith("codebuddy/")
            }
            with self.subTest(chain=chain):
                self.assertTrue(
                    codebuddy_models <= ALLOWED_CODEBUDDY_MODELS,
                    f"{chain} contains unsupported CodeBuddy candidates: {sorted(codebuddy_models)}",
                )

    def test_codebuddy_registers_only_hy3_and_sol(self):
        models_file = REAL_USER_HOME / ".omp" / "agent" / "models.yml"
        text = models_file.read_text(encoding="utf-8")
        codebuddy_block = text.split("  codebuddy:\n", 1)[1].split("\n  atomcode:\n", 1)[0]
        registered = {
            line.split(":", 1)[1].strip()
            for line in codebuddy_block.splitlines()
            if line.strip().startswith("- id:")
        }
        self.assertEqual(registered, ALLOWED_CODEBUDDY_MODELS)

    def test_omp_can_resolve_registered_models(self):
        env = os.environ.copy()
        env.update({"HOME": str(REAL_USER_HOME), "USERPROFILE": str(REAL_USER_HOME)})
        result = subprocess.run(
            ["omp", "models"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for provider in ("zg-newapi", "zg-newapi-anthropic", "codebuddy", "longcat"):
            self.assertIn(f"{provider} (", result.stdout)


if __name__ == "__main__":
    unittest.main()
