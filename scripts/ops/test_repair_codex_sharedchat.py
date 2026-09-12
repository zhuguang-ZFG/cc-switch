import unittest

from scripts.ops.repair_codex_sharedchat import repaired_rules


class RepairCodexSharedChatTests(unittest.TestCase):
    def test_codex_rule_allows_cross_channel_retry(self):
        rules = [{"name": "codex cli trace", "skip_retry_on_failure": True}, {"name": "claude trace", "skip_retry_on_failure": False}]
        result = repaired_rules(rules)
        self.assertFalse(result[0]["skip_retry_on_failure"])
        self.assertFalse(result[1]["skip_retry_on_failure"])


if __name__ == "__main__":
    unittest.main()
