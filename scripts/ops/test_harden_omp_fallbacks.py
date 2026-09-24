"""Configuration changes are isolated, reversible, and channel-aware."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import harden_omp_fallbacks as harden


CONFIG = """# preserve comments and private unrelated values
modelRoles:
  default: zg-newapi/kimi-for-coding:max
  plan: zg-newapi/k3:max
providers:
  maxInFlightRequests:
    zg-newapi: 6
task:
  maxConcurrency: 4
  maxRuntimeMs: 1800000 # native hard cap
retry:
  maxRetries: 3
  maxDelayMs: 90000
  fallbackChains:
    slow:
      - zg-newapi-anthropic/claude-opus-4-8
      - zg-newapi/k3
    plan:
      - zg-newapi-anthropic/claude-opus-4-8
      - zg-newapi-anthropic/intern-s2-preview
    zg-newapi/kimi-for-coding:
      - zg-newapi/k3
      - zg-newapi/deepseek-v4-flash
    smol:
      - zg-newapi/omen-alpha
      - zg-newapi/mercury-2
custom:
  sensitive: fixture-secret
"""
POSTURE = {"kimi-for-coding": [33], "k3": [33], "deepseek-v4-flash": [118],
           "intern-s2-preview": [66, 67], "claude-opus-4-8": []}


class HardeningTests(unittest.TestCase):
    def test_only_approved_fields_change_and_comments_survive(self):
        for newline in ("\n", "\r\n"):
            original = CONFIG.replace("\n", newline)
            result, report = harden.transform(original)
            before, after = harden.parse(original), harden.parse(result)
            for key in ("modelRoles", "custom"):
                self.assertEqual(before[key], after[key])
            self.assertEqual(after["retry"]["fallbackChains"]["smol"], before["retry"]["fallbackChains"]["smol"])
            self.assertEqual(after["task"]["maxConcurrency"], 4)
            self.assertEqual(after["retry"]["maxRetries"], 3)
            self.assertEqual(after["retry"]["fallbackChains"]["plan"], ["zg-newapi-anthropic/intern-s2-preview"])
            self.assertEqual(after["retry"]["fallbackChains"]["slow"], ["zg-newapi/k3"])
            self.assertEqual(after["providers"]["streamIdleTimeoutSeconds"], 60)
            self.assertEqual(after["task"]["maxRuntimeMs"], 900000)
            self.assertIn("# native hard cap", result)
            self.assertNotIn("fixture-secret", str(report))
            self.assertEqual(harden.transform(result)[0], result)
            if newline == "\r\n":
                self.assertNotIn("\n", result.replace("\r\n", ""))

    def test_duplicate_or_unreviewed_settings_fail_closed(self):
        invalid = [CONFIG + "retry: {}\n", CONFIG.replace("1800000", "3600000"),
                   CONFIG.replace("zg-newapi/deepseek-v4-flash", "provider/another"),
                   CONFIG.replace("      - zg-newapi/k3\n", "")]
        for value in invalid:
            with self.subTest(config_length=len(value)):
                with self.assertRaises((ValueError, RuntimeError)):
                    harden.transform(value)
        with self.assertRaises(ValueError) as error:
            harden.parse("credentials: [fixture-secret")
        self.assertNotIn("fixture-secret", str(error.exception))

    def test_channel_changes_require_fresh_review(self):
        harden.validate_posture(POSTURE)
        for key, value in [("claude-opus-4-8", [86]), ("k3", [115]),
                           ("deepseek-v4-flash", [33]), ("intern-s2-preview", [])]:
            posture = copy.deepcopy(POSTURE)
            posture[key] = value
            with self.assertRaises(ValueError):
                harden.validate_posture(posture)

    def test_backup_is_byte_identical_and_user_drift_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yml"
            original = CONFIG.encode()
            config.write_bytes(original)
            updated, report = harden.transform(CONFIG)
            backup = harden.apply_config(config, original, updated.encode(), report)
            self.assertEqual((backup / "config.yml").read_bytes(), original)
            self.assertEqual(config.read_bytes(), updated.encode())
            config.write_bytes(b"user edit")
            with self.assertRaises(ValueError):
                harden.apply_config(config, original, updated.encode(), report)
            self.assertEqual(config.read_bytes(), b"user edit")

    def test_failure_after_write_restores_verified_original(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yml"
            original = CONFIG.encode()
            config.write_bytes(original)
            updated, report = harden.transform(CONFIG)
            writer = harden.atomic_write
            count = 0

            def fail_after_first_write(path, data):
                nonlocal count
                count += 1
                writer(path, data)
                if count == 1:
                    raise OSError("injected after replacement")

            with patch.object(harden, "atomic_write", side_effect=fail_after_first_write):
                with self.assertRaises(OSError):
                    harden.apply_config(config, original, updated.encode(), report)
            self.assertEqual(config.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
