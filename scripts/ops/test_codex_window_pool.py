"""Protect the resource-window exception from hiding real channel failures."""

import atexit
import logging
import unittest
from unittest.mock import Mock

from scripts.ops.test_guardian import guardian, make_engine
from scripts.ops.codex_window_pool import (
    CHANNEL_NAME,
    MODELS,
    WINDOW_POOL_TAG,
    SHAREDCHAT_CHANNEL_NAME,
    SHAREDCHAT_POOL_TAG,
    SHAREDCHAT_BASE_URL,
    is_codex_probe_incompatible,
    is_window_budget_exhausted,
)

# Close the imported Guardian fixture's file handler before Windows removes
# its temporary HOME at interpreter shutdown.
atexit.register(logging.shutdown)


QUOTA_ERROR = "Budget pool quota has been exhausted. Please ask an administrator to increase the limit or select another budget pool."


def channel(**changes):
    return {
        "id": 127, "name": CHANNEL_NAME, "type": 1, "status": 1,
        "tag": WINDOW_POOL_TAG, "auto_ban": 0, "weight": 5, "priority": 40,
        "base_url": "https://agentrouter.org", "models": ",".join(MODELS),
        **changes,
    }


class WindowPoolPolicyTests(unittest.TestCase):
    def test_resource_shortage_survives_channel_local_status_mapping(self):
        for status in (402, 503):
            with self.subTest(status=status):
                self.assertTrue(is_window_budget_exhausted(channel(), f"HTTP {status}: {QUOTA_ERROR}"))

    def test_exemption_requires_exact_opt_in_and_upstream(self):
        cases = (
            {"name": "agentrouter"}, {"tag": ""}, {"auto_ban": 1},
            {"type": 14}, {"base_url": "https://agentrouter.org.example.com"},
            {"base_url": "https://example.com"}, {"models": "claude-opus-5"},
            {"models": "gpt-6-astra,glm-5.3"}, {"models": ""},
        )
        for change in cases:
            with self.subTest(change=change):
                self.assertFalse(is_window_budget_exhausted(channel(**change), QUOTA_ERROR))

    def test_real_authentication_and_balance_failures_remain_fatal(self):
        for error in ("401 invalid_api_key", "402 insufficient balance", "403 account suspended", "503 upstream unavailable"):
            with self.subTest(error=error):
                self.assertFalse(is_window_budget_exhausted(channel(), error))

    def test_both_scanners_preserve_window_channel_and_clear_failure_streak(self):
        for full_scan in (False, True):
            with self.subTest(full_scan=full_scan):
                engine = make_engine()
                engine._scan_offset = engine._full_scan_offset = 0
                engine.newapi.channels[127] = channel()
                engine.newapi.test_results.append((False, f"HTTP 503: {QUOTA_ERROR}"))
                engine._probe_soft_failures[127] = 2
                engine.telegram = Mock()
                if full_scan:
                    engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
                    engine.full_health_scan()
                else:
                    engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
                    engine.scan_error_channels()
                self.assertEqual(engine.newapi.disable_calls, [])
                self.assertEqual(engine.newapi.updates, [])
                self.assertNotIn(127, engine._probe_soft_failures)
                engine.telegram.send_alert.assert_not_called()

    def test_invalid_key_on_window_channel_still_quarantines(self):
        for full_scan in (False, True):
            with self.subTest(full_scan=full_scan):
                engine = make_engine()
                engine._scan_offset = engine._full_scan_offset = 0
                engine.newapi.channels[127] = channel()
                engine.newapi.test_results.append((False, "401 invalid_api_key"))
                engine.telegram = Mock()
                if full_scan:
                    engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
                    engine.full_health_scan()
                else:
                    engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
                    engine.scan_error_channels()
                self.assertEqual(engine.newapi.disable_calls, [127])

    def test_sharedchat_exempts_only_site_quota_and_native_probe_shape(self):
        shared = channel(id=128, name=SHAREDCHAT_CHANNEL_NAME, tag=SHAREDCHAT_POOL_TAG, base_url=SHAREDCHAT_BASE_URL, models=','.join(MODELS))
        self.assertTrue(is_window_budget_exhausted(shared, "503 global_fixed_window_quota_exhausted"))
        self.assertFalse(is_window_budget_exhausted(shared, QUOTA_ERROR))
        self.assertFalse(is_window_budget_exhausted(shared, "insufficient_balance"))
        self.assertFalse(is_window_budget_exhausted(shared, "invalid_api_key global_fixed_window_quota_exhausted"))
        self.assertTrue(is_codex_probe_incompatible(shared, "403 codex_access_restricted"))
        self.assertTrue(is_codex_probe_incompatible(shared, "请使用最新版本的codex客户端或codex cli调用"))
        self.assertFalse(is_codex_probe_incompatible(shared, "invalid_api_key codex_access_restricted"))
        self.assertFalse(is_codex_probe_incompatible(channel(), "codex_access_restricted"))
        self.assertFalse(is_window_budget_exhausted({**shared, "name": "sharedchat-codex-sol"}, "global_fixed_window_quota_exhausted"))
        self.assertFalse(is_window_budget_exhausted({**shared, "base_url": "https://new.sharedchat.cc.example.com/codex"}, "global_fixed_window_quota_exhausted"))

    def test_sharedchat_scanners_keep_quota_but_quarantine_invalid_credentials(self):
        for full_scan in (False, True):
            for message, disabled in (("global_fixed_window_quota_exhausted", False), ("codex_access_restricted", False), ("401 invalid_api_key", True)):
                with self.subTest(full_scan=full_scan, message=message):
                    engine = make_engine()
                    engine._scan_offset = engine._full_scan_offset = 0
                    engine.newapi.channels[128] = channel(id=128, name=SHAREDCHAT_CHANNEL_NAME, tag=SHAREDCHAT_POOL_TAG, base_url=SHAREDCHAT_BASE_URL, models=','.join(MODELS))
                    engine.newapi.test_results.append((False, message))
                    engine._probe_soft_failures[128] = 2
                    engine.telegram = Mock()
                    if full_scan:
                        engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
                        engine.full_health_scan()
                    else:
                        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
                        engine.scan_error_channels()
                    self.assertEqual(engine.newapi.disable_calls, [128] if disabled else [])
                    if not disabled:
                        self.assertNotIn(128, engine._probe_soft_failures)
                        engine.telegram.send_alert.assert_not_called()

    def test_sharedchat_admin_probe_uses_streaming_responses(self):
        client = guardian.NewAPIClient("http://127.0.0.1:3002", "fixture-token", "1")
        client._request = Mock(return_value={"success": False, "message": "codex_access_restricted"})
        self.assertEqual(client.test_channel(128), (False, "codex_access_restricted"))
        client._request.assert_called_once_with("GET", "/api/channel/test/128?model=gpt-5.6-sol&endpoint_type=openai-response&stream=true", timeout=guardian.TEST_CHANNEL_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
