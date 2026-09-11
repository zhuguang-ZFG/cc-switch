import unittest

from scripts.ops.repair_codex_sharedchat import repaired_rules


class RepairCodexSharedChatTests(unittest.TestCase):
    def test_only_codex_rule_disables_cross_channel_retry(self):
        rules = [{"name": "codex cli trace", "skip_retry_on_failure": False}, {"name": "claude trace", "skip_retry_on_failure": False}]
        result = repaired_rules(rules)
        self.assertTrue(result[0]["skip_retry_on_failure"])
        self.assertFalse(result[1]["skip_retry_on_failure"])


if __name__ == "__main__":
    unittest.main()
