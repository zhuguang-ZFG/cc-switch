"""Narrow policy for the native Codex resource pools; no global exemptions."""

from __future__ import annotations

from urllib.parse import urlsplit


CHANNEL_NAME = "agentrouter-codex-gpt"
WINDOW_POOL_TAG = "codex-resource-window"
MODELS = ("gpt-6-astra", "gpt-5.6-sol")
PRIORITY = 40
WEIGHT = 5
ANY_CHANNEL_ID = 126
RETRY_MAPPING = {"402": "503", "429": "503"}
RESOURCE_HOURS_BEIJING = (0, 8, 16)
SHAREDCHAT_CHANNEL_NAME = "sharedchat-codex-astra"
SHAREDCHAT_POOL_TAG = "codex-sitewide-pool"
SHAREDCHAT_BASE_URL = "https://new.sharedchat.cc/codex"
SHAREDCHAT_PRIORITY = 60
SHAREDCHAT_RETRY_MAPPING = {**RETRY_MAPPING, "403": "503"}
SHAREDCHAT_CHANNEL_ID = 128  # Local deployment contract, checked before enable.


def is_sharedchat_pool(channel: dict) -> bool:
    return (
        channel.get("name") == SHAREDCHAT_CHANNEL_NAME
        and channel.get("tag") == SHAREDCHAT_POOL_TAG
        and channel.get("type") == 1
        and str(channel.get("auto_ban")).lower() in {"0", "false"}
        and (channel.get("base_url") or "").rstrip("/") == SHAREDCHAT_BASE_URL
        and {item.strip() for item in (channel.get("models") or "").split(",") if item.strip()} == set(MODELS)
    )


def is_window_budget_exhausted(channel: dict, message: str) -> bool:
    """A scheduled resource shortage is not a permanent credential failure.

    This exemption is deliberately limited to our opt-in GPT channel. The
    existing AgentRouter Claude/GLM channels and ordinary billing failures
    retain Guardian's normal quarantine/recovery behavior.
    """
    lowered = (message or "").lower()
    if any(marker in lowered for marker in ("invalid_api_key", "invalid api key", "invalid_token", "invalid token", "insufficient_balance", "余额不足", "account suspended")):
        return False
    if is_sharedchat_pool(channel):
        # SharedChat is site-wide quota, not AgentRouter's 00/08/16 schedule.
        return "global_fixed_window_quota_exhausted" in lowered
    if (
        channel.get("name") != CHANNEL_NAME
        or channel.get("tag") != WINDOW_POOL_TAG
        or channel.get("type") != 1
        or str(channel.get("auto_ban")).lower() not in {"0", "false"}
    ):
        return False
    try:
        host = urlsplit(channel.get("base_url") or "").hostname
    except ValueError:
        return False
    models = {item.strip() for item in (channel.get("models") or "").split(",") if item.strip()}
    return (
        host in {"agentrouter.org", "ps.air-outer.com"}
        and bool(models)
        and models.issubset(MODELS)
        and "budget pool quota has been exhausted" in (message or "").lower()
    )


def is_codex_probe_incompatible(channel: dict, message: str) -> bool:
    """SharedChat's generic admin probe does not prove native CLI health."""
    if not is_sharedchat_pool(channel):
        return False
    lowered = (message or "").lower()
    if any(marker in lowered for marker in ("invalid_api_key", "invalid api key", "insufficient_balance", "余额不足")):
        return False
    return "codex_access_restricted" in lowered or "请使用最新版本的codex客户端或codex cli调用" in lowered
