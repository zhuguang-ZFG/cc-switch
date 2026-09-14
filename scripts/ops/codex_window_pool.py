"""Narrow policy for the native Codex resource pools; no global exemptions."""

from __future__ import annotations

from urllib.parse import urlsplit


CHANNEL_NAME = "agentrouter-codex-gpt"
WINDOW_POOL_TAG = "codex-resource-window"
MODELS = ("gpt-6-astra", "gpt-5.6-sol")
PRIORITY = 40
WEIGHT = 5
ANY_CHANNEL_ID = 126
ANY_CHANNEL_NAME = "any-gpt-6-astra"
ANY_POOL_HOST = "anyrouter.top"
RETRY_MAPPING = {"402": "503", "429": "503"}
RESOURCE_HOURS_BEIJING = (0, 8, 16)
SHAREDCHAT_CHANNEL_NAME = "sharedchat-codex-astra"
SHAREDCHAT_POOL_TAG = "codex-sitewide-pool"
SHAREDCHAT_BASE_URL = "https://new.sharedchat.cc/codex"
SHAREDCHAT_PRIORITY = 60
SHAREDCHAT_RETRY_MAPPING = {**RETRY_MAPPING, "403": "503"}
SHAREDCHAT_CHANNEL_ID = 128  # Local deployment contract, checked before enable.
WINDOW_POOL_HOSTS = frozenset({"agentrouter.org", "ps.air-outer.com"})


def _pool_models(channel: dict) -> set[str]:
    return {item.strip() for item in (channel.get("models") or "").split(",") if item.strip()}


def is_sharedchat_pool(channel: dict) -> bool:
    return (
        channel.get("name") == SHAREDCHAT_CHANNEL_NAME
        and channel.get("tag") == SHAREDCHAT_POOL_TAG
        and channel.get("type") == 1
        and str(channel.get("auto_ban")).lower() in {"0", "false"}
        and (channel.get("base_url") or "").rstrip("/") == SHAREDCHAT_BASE_URL
        and bool(_pool_models(channel))
    )


def is_window_pool(channel: dict) -> bool:
    """AgentRouter opt-in identity: name + tag + type + auto_ban + upstream host.

    The model roster is deliberately excluded. Welding identity to an exact
    model tuple meant every roster edit silently evaluated False, dropped the
    exemption, and let a scheduled 0/8/16 shortage quarantine the channel
    (09-12: ch128 shipped two models against a one-model comparison).
    """
    if (
        channel.get("name") != CHANNEL_NAME
        or channel.get("tag") != WINDOW_POOL_TAG
        or channel.get("type") != 1
        or str(channel.get("auto_ban")).lower() not in {"0", "false"}
        or not _pool_models(channel)
    ):
        return False
    try:
        return urlsplit(channel.get("base_url") or "").hostname in WINDOW_POOL_HOSTS
    except ValueError:
        return False


def pool_model_drift(channel: dict) -> tuple[str, ...]:
    """Pool models this policy did not expect, sorted; empty when reconciled.

    Drift no longer breaks the exemption, so it must stay observable: callers
    alert once per roster so MODELS and the live channel get reconciled on
    purpose instead of through a silent quarantine.
    """
    if not (is_sharedchat_pool(channel) or is_window_pool(channel)):
        return ()
    return tuple(sorted(_pool_models(channel) - set(MODELS)))


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
    if not is_window_pool(channel):
        return False
    window_quota_markers = (
        # 402 预算池（09-12 起豁免）+ 403 账号时段配额（09-13 现场：投放窗口外
        # agentrouter 对 test/real 流量均可返回 "user quota is not enough"，
        # 同属 0/8/16 投放制，保持启用让流量自然等到投放点，禁用只会制造空洞）。
        "budget pool quota has been exhausted",
        "user quota is not enough",
    )
    return any(marker in lowered for marker in window_quota_markers)


def is_any_pool(channel: dict) -> bool:
    """AnyRouter's Codex-only gpt-6-astra leg; roster deliberately excluded.

    Pinned by deployment id *and* name *and* host, so a renamed, moved, or
    re-numbered channel loses the exemption instead of inheriting it.
    """
    if (
        channel.get("name") != ANY_CHANNEL_NAME
        or str(channel.get("id")) != str(ANY_CHANNEL_ID)
        or channel.get("type") != 1
        or not _pool_models(channel)
    ):
        return False
    try:
        return urlsplit(channel.get("base_url") or "").hostname == ANY_POOL_HOST
    except ValueError:
        return False


def is_codex_probe_incompatible(channel: dict, message: str) -> bool:
    """A probe shape the upstream rejects is not a channel health verdict.

    SharedChat's admin probe and AnyRouter's Codex-only gpt-6-astra both answer
    the generic probe by rejecting the *request*, not the channel. ch126 showed
    the cost: a recurring 404 "当前 API 不支持所选模型" carries no
    "invalid_request_error", so Guardian's generic check missed it, three soft
    failures degraded weight 5->2, while real billed traffic was 0% empty in the
    same window. Scoped per pool: that 404 stays fatal for every other channel.
    """
    lowered = (message or "").lower()
    if any(marker in lowered for marker in ("invalid_api_key", "invalid api key", "insufficient_balance", "余额不足")):
        return False
    if is_any_pool(channel):
        return "当前 api 不支持所选模型" in lowered
    if not is_sharedchat_pool(channel):
        return False
    return "codex_access_restricted" in lowered or "请使用最新版本的codex客户端或codex cli调用" in lowered
