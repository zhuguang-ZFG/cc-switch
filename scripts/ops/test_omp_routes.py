"""OMP 大工程路由静态门禁。"""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


REAL_USER_HOME = Path(os.environ.get("OMP_REAL_HOME", "C:/Users/zhugu"))
CONFIG_FILE = REAL_USER_HOME / ".omp" / "agent" / "config.yml"
MODELS_FILE = REAL_USER_HOME / ".omp" / "agent" / "models.yml"
FORBIDDEN_CRITICAL_CANDIDATES = {
    "agentrouter/claude-opus-5",
    "agentrouter/claude-opus-4-8",
}
REMOVED_LOCAL_PROVIDERS = {"codebuddy"}
_THINKING_SUFFIXES = frozenset(("minimal", "low", "medium", "high", "xhigh", "max", "auto"))


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

def _top_level_mapping_block(text: str, key: str) -> str:
    """提取 YAML 顶层 mapping 的单个二级块，不依赖后续 key 顺序。"""
    marker = f"  {key}:"
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.rstrip() == marker),
        None,
    )
    if start is None:
        raise AssertionError(f"missing provider block: {key}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.strip():
            end = index
            break
    return "\n".join(lines[start:end])

def _model_role_entries(text: str) -> dict[str, str]:
    """从 config.yml 提取 modelRoles；保持依赖为标准库。"""
    result: dict[str, str] = {}
    in_roles = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped == "modelRoles:":
            in_roles = True
            continue
        if in_roles and indent == 0 and stripped:
            break
        if in_roles and indent == 2 and ":" in stripped:
            role, selector = stripped.split(":", 1)
            result[role] = selector.strip()
    return result


def _base_selector(selector: str) -> str:
    """去掉 OMP thinking 后缀，保留 provider/model。"""
    base, separator, suffix = selector.rpartition(":")
    if separator and suffix in _THINKING_SUFFIXES:
        return base
    return selector


def _parse_disabled_providers(text: str) -> list[str]:
    """提取 config.yml 顶层 disabledProviders 全局列表。"""
    result: list[str] = []
    in_block = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped == "disabledProviders:":
            in_block = True
            continue
        if not in_block:
            continue
        if indent == 0 or (indent == 2 and stripped and not stripped.startswith("- ")):
            break
        if indent == 2 and stripped.startswith("- "):
            result.append(stripped[2:].strip())
    return result


def _parse_bool(value: str) -> bool | None:
    """解析布尔能力字段；无法解析视为 UNKNOWN（None）。"""
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _parse_int(value: str) -> int | str | None:
    """显式数值字段：可解析为 int，否则保留原始字符串；缺失为 None。"""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _parse_model_registrations(text: str) -> dict[str, list[dict[str, object]]]:
    """解析 models.yml 为 provider -> 模型能力列表。

    只暴露 id/supportsTools/input/contextWindow/maxTokens；apiKey 等传输密钥
    一律跳过，保证任何断言与报告都不会泄漏密钥。
    """
    providers: dict[str, list[dict[str, object]]] = {}
    provider: str | None = None
    model: dict[str, object] | None = None
    input_items: list[str] | None = None

    def flush_input() -> None:
        nonlocal input_items
        if model is not None and input_items is not None:
            model["input"] = input_items
        input_items = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 0:
            flush_input()
            provider = None
            model = None
            continue
        if indent == 2:
            flush_input()
            model = None
            if stripped.endswith(":"):
                provider = stripped[:-1].strip()
                providers.setdefault(provider, [])
            continue
        if indent == 4:
            if stripped.startswith("- id:"):
                flush_input()
                model_id = stripped[len("- id:"):].strip().strip("\"'")
                model = {
                    "id": model_id,
                    "supportsTools": None,
                    "input": None,
                    "contextWindow": None,
                    "maxTokens": None,
                }
                providers.setdefault(provider, []).append(model)
            # apiKey / baseUrl / api / authHeader / models: 忽略
            continue
        if indent == 6 and model is not None:
            if stripped.startswith("- "):
                if input_items is not None:
                    input_items.append(stripped[2:].strip().strip("\"'"))
                continue
            key, separator, value = stripped.partition(":")
            if not separator:
                continue
            flush_input()
            key = key.strip()
            value = value.strip()
            if key == "input":
                if value:
                    if value.startswith("["):
                        cleaned = value.strip().strip("[]").replace(",", " ")
                        model["input"] = [
                            item.strip().strip("\"'") for item in cleaned.split() if item.strip()
                        ]
                    # 其他标量形态视为未知，保持 None
                else:
                    input_items = []
            elif key == "supportsTools":
                model["supportsTools"] = _parse_bool(value)
            elif key == "contextWindow":
                model["contextWindow"] = _parse_int(value)
            elif key == "maxTokens":
                model["maxTokens"] = _parse_int(value)
    flush_input()
    return providers


def _parse_selector(selector: str) -> tuple[str, str] | None:
    """规范化后拆分为 (provider, model)。

    provider/* 是唯一合法通配；其余含 '*'、缺 '/'、空段或遗留 ':' 的
    selector 均为 malformed（返回 None）。
    """
    if "/" not in selector:
        return None
    provider, model_id = selector.split("/", 1)
    if not provider or not model_id:
        return None
    if ":" in model_id or "*" in provider:
        return None
    if model_id == "*":
        return (provider, "*")
    if "*" in model_id:
        return None
    return (provider, model_id)


def _registration_index(
    registrations: dict[str, list[dict[str, object]]],
) -> dict[str, dict[str, dict[str, object]]]:
    """provider -> {model_id -> capabilities}，便于 O(1) 查找。"""
    return {
        provider: {model["id"]: model for model in models}
        for provider, models in registrations.items()
    }


def _omp_models_resolves(output: str, provider: str, model_id: str) -> bool:
    """在 `omp models` 表格输出中定位 provider 块内的模型单元格。"""
    if not output:
        return False
    header = f"{provider} ("
    cell = f"│ {model_id} "
    box_chars = ("│", "┌", "├", "└", "─")
    in_block = False
    for raw_line in output.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(header):
            in_block = True
            continue
        if not in_block:
            continue
        if not stripped:
            continue
        if not stripped.startswith(box_chars):
            in_block = False
            continue
        if cell in stripped:
            return True
    return False


def _tool_centric_chain_keys(roles: dict[str, str]) -> set[str]:
    """default/slow/plan/task/designer 及其主模型的精确 selector 链视为 tool-centric。"""
    tool_centric_roles = frozenset(("default", "slow", "plan", "task", "designer"))
    keys = set(tool_centric_roles)
    for role, selector in roles.items():
        if role in tool_centric_roles:
            keys.add(_base_selector(selector))
    return keys


def validate_fallback_chains(
    config_text: str,
    models_text: str,
    omp_models_output: str = "",
) -> tuple[list[str], list[str]]:
    """校验全部 fallback chain，返回 (hard violations, warnings)。

    hard 失败：malformed selector、未注册且 `omp models` 不可解析的 selector、
    链内规范化重复、全局禁用 provider、tool-centric 链显式 supportsTools: false、
    vision 链显式 input 缺 image、显式 contextWindow/maxTokens 非正。
    缺失能力元数据只产生确定性 warning（UNKNOWN 不等于 unsupported）。
    """
    violations: list[str] = []
    warnings: list[str] = []
    registrations = _parse_model_registrations(models_text)
    index = _registration_index(registrations)
    chains = _fallback_chain_entries(config_text)
    roles = _model_role_entries(config_text)
    disabled = set(_parse_disabled_providers(config_text))
    tool_centric = _tool_centric_chain_keys(roles)

    for provider, models in registrations.items():
        for model in models:
            qualified = f"{provider}/{model['id']}"
            for field in ("contextWindow", "maxTokens"):
                value = model[field]
                if value is None:
                    continue
                if not isinstance(value, int) or value <= 0:
                    violations.append(
                        f"models.yml: {qualified} {field} must be positive, got {value!r}"
                    )

    for chain, candidates in chains.items():
        tool_centric_chain = _base_selector(chain) in tool_centric
        vision_chain = chain == "vision"
        seen: set[str] = set()
        for raw in candidates:
            base = _base_selector(raw)
            if base in seen:
                violations.append(f"{chain}: duplicate normalized candidate {base!r}")
                continue
            seen.add(base)
            parsed = _parse_selector(base)
            if parsed is None:
                violations.append(f"{chain}: malformed selector {raw!r}")
                continue
            provider, model_id = parsed
            if provider in disabled:
                violations.append(f"{chain}: provider {provider} is globally disabled")
            if model_id == "*":
                if index.get(provider):
                    warnings.append(f"{chain}: wildcard {base!r} cannot be resolved statically")
                else:
                    warnings.append(
                        f"{chain}: wildcard {base!r} provider {provider} has no registered models"
                    )
                continue
            registered = (index.get(provider) or {}).get(model_id)
            if registered is None:
                if _omp_models_resolves(omp_models_output, provider, model_id):
                    if tool_centric_chain:
                        warnings.append(
                            f"{chain}: {base} has unknown supportsTools metadata (resolved via omp models)"
                        )
                    if vision_chain:
                        warnings.append(
                            f"{chain}: {base} has unknown input modalities (resolved via omp models)"
                        )
                    continue
                violations.append(
                    f"{chain}: selector {base!r} is not registered in custom models nor resolvable from omp models"
                )
                continue
            if tool_centric_chain:
                supports = registered.get("supportsTools")
                if supports is False:
                    violations.append(
                        f"{chain}: {base} declares supportsTools: false (tool-centric chain)"
                    )
                elif supports is None:
                    warnings.append(
                        f"{chain}: {base} has unknown supportsTools metadata (tool-centric chain)"
                    )
            if vision_chain:
                inputs = registered.get("input")
                if inputs is None:
                    warnings.append(f"{chain}: {base} has unknown input modalities (vision chain)")
                elif "image" not in inputs:
                    violations.append(
                        f"{chain}: {base} declares input without image (vision chain)"
                    )
    return violations, warnings


def _model_block(model_id: str, **fields: object) -> str:
    """构造 models.yml 单个模型条目文本（fixture 专用）。"""
    lines = [f"    - id: {model_id}"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"      {key}:")
            lines.extend(f"      - {item}" for item in value)
        elif isinstance(value, bool):
            lines.append(f"      {key}: {str(value).lower()}")
        else:
            lines.append(f"      {key}: {value}")
    return "\n".join(lines) + "\n"


def _models_yml(*model_blocks: str, provider: str = "zg-newapi") -> str:
    """构造最小 models.yml fixture（带假 apiKey 用于验证忽略逻辑）。"""
    return (
        "providers:\n"
        f"  {provider}:\n"
        "    baseUrl: http://127.0.0.1:3002/v1\n"
        "    api: openai-completions\n"
        "    apiKey: FIXTURE_SECRET_MUST_NEVER_SURFACE\n"
        "    authHeader: true\n"
        "    models:\n"
        + "".join(model_blocks)
    )


def _config_yml(
    chains: dict[str, list[str]],
    *,
    roles: dict[str, str] | None = None,
    disabled: list[str] | None = None,
) -> str:
    """构造最小 config.yml fixture（与 _fallback_chain_entries 的缩进形状一致）。"""
    lines: list[str] = []
    if roles:
        lines.append("modelRoles: ")
        lines.extend(f"  {role}: {selector}" for role, selector in roles.items())
    lines.append("disabledProviders: ")
    lines.extend(f"  - {name}" for name in (disabled or []))
    lines.append("retry: ")
    lines.append("  modelFallback: true")
    lines.append("  fallbackRevertPolicy: cooldown-expiry")
    lines.append("  fallbackChains: ")
    for chain, candidates in chains.items():
        lines.append(f"    {chain}: ")
        lines.extend(f"      - {candidate}" for candidate in candidates)
    return "\n".join(lines) + "\n"


class OmpRouteGateTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not CONFIG_FILE.exists():
            raise unittest.SkipTest(f"local OMP config not present: {CONFIG_FILE}")
        if shutil.which("omp") is None:
            raise unittest.SkipTest("omp CLI is not installed")
        env = os.environ.copy()
        env.update({"HOME": str(REAL_USER_HOME), "USERPROFILE": str(REAL_USER_HOME)})
        result = subprocess.run(
            ["omp", "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            env=env,
        )
        cls.omp_models_rc = result.returncode
        cls.omp_models_output = result.stdout
        cls.omp_models_stderr = result.stderr

    def test_model_fallback_is_enabled(self):
        text = CONFIG_FILE.read_text(encoding="utf-8")
        self.assertIn("  modelFallback: true", text)
        self.assertIn("  fallbackRevertPolicy: cooldown-expiry", text)

    def test_default_role_has_no_model_fallback_chain(self):
        """default 可使用聚合渠道重试，但不得切换到另一个模型。"""
        text = CONFIG_FILE.read_text(encoding="utf-8")
        roles = _model_role_entries(text)
        chains = _fallback_chain_entries(text)
        self.assertIn("default", roles, "modelRoles.default must be configured")
        primary = _base_selector(roles["default"])
        configured = sorted({"default", primary} & set(chains))
        self.assertEqual(
            configured,
            [],
            "default must hard-fail after its provider/pool is exhausted; "
            f"remove model fallback chains {configured}",
        )


    def test_critical_chains_exclude_known_bad_agentrouter_claude(self):
        chains = _fallback_chain_entries(CONFIG_FILE.read_text(encoding="utf-8"))
        for role, candidates in chains.items():
            with self.subTest(role=role):
                self.assertTrue(candidates, f"{role} fallback chain must not be empty")
                self.assertTrue(
                    FORBIDDEN_CRITICAL_CANDIDATES.isdisjoint(candidates),
                    f"{role} contains known-sensitive-word AgentRouter Claude route: {candidates}",
                )

    def test_fallback_chain_keys_are_resolvable(self):
        """链键必须是已配置角色、default 或精确 provider/model；禁止无消费者死配置。"""
        text = CONFIG_FILE.read_text(encoding="utf-8")
        roles = _model_role_entries(text)
        chains = _fallback_chain_entries(text)
        orphaned = sorted(
            key
            for key in chains
            if key != "default" and key not in roles and "/" not in key
        )
        self.assertEqual(orphaned, [], f"unresolvable fallback chain keys: {orphaned}")

    def test_role_fallbacks_do_not_repeat_their_primary_model(self):
        text = CONFIG_FILE.read_text(encoding="utf-8")
        roles = _model_role_entries(text)
        chains = _fallback_chain_entries(text)
        for role, candidates in chains.items():
            if role not in roles:
                continue
            primary = _base_selector(roles[role])
            fallback_models = {_base_selector(candidate) for candidate in candidates}
            with self.subTest(role=role):
                self.assertNotIn(
                    primary,
                    fallback_models,
                    f"{role} fallback repeats primary model {primary}: {candidates}",
                )

    def test_unhealthy_anyrouter_is_not_an_automatic_fallback(self):
        chains = _fallback_chain_entries(CONFIG_FILE.read_text(encoding="utf-8"))
        routed = [
            chain
            for chain, candidates in chains.items()
            if any(candidate.startswith("anyrouter/") for candidate in candidates)
        ]
        self.assertEqual(
            routed,
            [],
            f"AnyRouter is upstream-429 and must remain manual-canary only: {routed}",
        )
    def test_anthropic_provider_uses_semantic_ttft_gateway(self):
        text = MODELS_FILE.read_text(encoding="utf-8")
        block = _top_level_mapping_block(text, "zg-newapi-anthropic")
        self.assertIn("baseUrl: http://127.0.0.1:3003", block)
        self.assertIn("api: anthropic-messages", block)
        self.assertNotIn("apiKey: PROXY_MANAGED", block)

    def test_claude_models_do_not_promote_to_non_claude_models(self):
        """选择 Claude 后不得因上下文提升静默切换为 DeepSeek 等其他模型。"""
        text = MODELS_FILE.read_text(encoding="utf-8")
        for provider in ("zg-newapi-anthropic", "agentrouter"):
            block = _top_level_mapping_block(text, provider)
            with self.subTest(provider=provider):
                self.assertFalse(
                    "contextPromotionTarget:" in block,
                    f"{provider} Claude models must stay Claude during context recovery",
                )

    def test_opus5_gateway_window_matches_official_capability(self):
        """Opus 5 的模型元数据必须表达官方 200k 上下文能力。

        2026-08-09 曾在 Kiro/NewAPI 聚合链路观察到约 130k-140k token 的
        确定性 400。那是链路承载风险，不是模型能力；不得用降低 contextWindow
        的方式把链路限制伪装成官方模型限制。该风险保留在运维文档和链路监控中。
        """
        text = MODELS_FILE.read_text(encoding="utf-8")
        registrations = _parse_model_registrations(text)
        index = _registration_index(registrations)
        opus5 = (index.get("zg-newapi-anthropic") or {}).get("claude-opus-5")
        self.assertIsNotNone(opus5, "zg-newapi-anthropic/claude-opus-5 must be registered")
        window = opus5["contextWindow"]
        self.assertIsNotNone(window, "claude-opus-5 contextWindow must be explicit")
        self.assertEqual(
            window,
            200000,
            f"claude-opus-5 contextWindow {window} must match the official 200k capability; "
            "route-specific upstream limits belong in operational health policy",
        )



    def test_removed_local_providers_are_not_registered_or_routed(self):
        chains = _fallback_chain_entries(CONFIG_FILE.read_text(encoding="utf-8"))
        registrations = _parse_model_registrations(
            MODELS_FILE.read_text(encoding="utf-8")
        )
        self.assertTrue(
            REMOVED_LOCAL_PROVIDERS.isdisjoint(registrations),
            f"removed providers remain registered: "
            f"{sorted(REMOVED_LOCAL_PROVIDERS & set(registrations))}",
        )
        for chain, candidates in chains.items():
            removed = {
                candidate.split("/", 1)[0]
                for candidate in candidates
                if "/" in candidate
                and candidate.split("/", 1)[0] in REMOVED_LOCAL_PROVIDERS
            }
            with self.subTest(chain=chain):
                self.assertEqual(
                    removed,
                    set(),
                    f"{chain} routes through removed providers: {sorted(removed)}",
                )

    def test_qwen38_max_registration_matches_channel_contract(self):
        """aliyun-qwen38 必须按官方 1M/128K reasoning+vision 能力注册。"""
        block = _top_level_mapping_block(MODELS_FILE.read_text(encoding="utf-8"), "zg-newapi")
        expected = (
            "    - id: qwen3.8-max\n"
            "      name: Qwen 3.8 Max (Aliyun Token Plan ch31)\n"
            "      reasoning: true\n"
            "      input:\n"
            "      - text\n"
            "      - image\n"
            "      contextWindow: 1000000\n"
            "      maxTokens: 131072"
        )
        self.assertTrue(
            expected in block,
            "zg-newapi/qwen3.8-max registration is missing or has incorrect capabilities",
        )

    def test_omp_can_resolve_registered_models(self):
        self.assertEqual(self.omp_models_rc, 0, self.omp_models_stderr)
        for provider in (
            "zg-newapi",
            "zg-newapi-anthropic",
            "agentrouter",
            "longcat",
            "anyrouter",
        ):
            self.assertIn(f"{provider} (", self.omp_models_output)

    def test_parse_disabled_providers_extracts_global_list(self):
        config = _config_yml({"bigctx": []}, disabled=["openai", "deepseek", "anthropic"])
        self.assertEqual(
            _parse_disabled_providers(config),
            ["openai", "deepseek", "anthropic"],
        )

    def test_parse_model_registrations_captures_capability_fields(self):
        models = _models_yml(
            _model_block(
                "gpt-5.6-sol",
                supportsTools=True,
                input=["text", "image"],
                contextWindow=1048576,
                maxTokens=128000,
            ),
        )
        parsed = _parse_model_registrations(models)
        model = parsed["zg-newapi"][0]
        self.assertEqual(model["id"], "gpt-5.6-sol")
        self.assertIs(model["supportsTools"], True)
        self.assertEqual(model["input"], ["text", "image"])
        self.assertEqual(model["contextWindow"], 1048576)
        self.assertEqual(model["maxTokens"], 128000)

    def test_parse_model_registrations_never_surfaces_api_key(self):
        secret = "sk-FIXTURE-SECRET-0123456789"
        models = (
            "providers:\n"
            "  zg-newapi:\n"
            "    baseUrl: http://127.0.0.1:3002/v1\n"
            "    api: openai-completions\n"
            f"    apiKey: {secret}\n"
            "    authHeader: true\n"
            "    models:\n"
            + _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000)
        )
        self.assertIn(secret, models)  # 夹具确实包含密钥，才能证明解析器忽略它
        parsed = _parse_model_registrations(models)
        self.assertNotIn(secret, repr(parsed))
        self.assertNotIn("apiKey", repr(parsed))

    def test_base_selector_normalizes_thinking_suffixes(self):
        self.assertEqual(_base_selector("zg-newapi/gpt-5.6-sol:high"), "zg-newapi/gpt-5.6-sol")
        self.assertEqual(_base_selector("zg-newapi/gpt-5.6-sol:max"), "zg-newapi/gpt-5.6-sol")
        self.assertEqual(_base_selector("zg-newapi/gpt-5.6-sol"), "zg-newapi/gpt-5.6-sol")
        self.assertEqual(
            _base_selector("zg-newapi/gpt-5.6-sol:unknown"),
            "zg-newapi/gpt-5.6-sol:unknown",
        )

    def test_parse_selector_accepts_valid_shapes_and_wildcard(self):
        self.assertEqual(
            _parse_selector("zg-newapi/gpt-5.6-sol"), ("zg-newapi", "gpt-5.6-sol")
        )
        self.assertEqual(
            _parse_selector("agentrouter/Qwen/Qwen3-VL-8B-Instruct"),
            ("agentrouter", "Qwen/Qwen3-VL-8B-Instruct"),
        )
        self.assertEqual(_parse_selector("zg-newapi/*"), ("zg-newapi", "*"))
        for bad in (
            "no-slash",
            "provider/",
            "/model",
            "provider/model:oops",
            "*/gpt-5",
            "zg-newapi/gpt-*",
            "*/*",
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(_parse_selector(bad))

    def test_malformed_selector_is_hard_violation(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        for selector in ("no-slash", "provider/", "/model", "provider/model:unknown", "*/gpt-5"):
            with self.subTest(selector=selector):
                config = _config_yml({"bigctx": [selector]})
                violations, warnings = validate_fallback_chains(config, models, "")
                self.assertEqual(len(violations), 1, violations)
                self.assertIn("malformed selector", violations[0])
                self.assertEqual(warnings, [])

    def test_unregistered_selector_is_hard_violation(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml({"bigctx": ["zg-newapi/ghost-model"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(
            violations,
            [
                "bigctx: selector 'zg-newapi/ghost-model' is not registered in custom models "
                "nor resolvable from omp models",
            ],
        )
        self.assertEqual(warnings, [])

    def test_omp_models_output_resolves_unregistered_selector(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml({"default": ["zg-newapi/ghost-model"]})
        omp_output = (
            "zg-newapi (2)\n"
            "┌────────────────────┐\n"
            "│ model              │\n"
            "├────────────────────┤\n"
            "│ ghost              │\n"
            "│ ghost-model        │\n"
            "└────────────────────┘\n"
        )
        violations, warnings = validate_fallback_chains(config, models, omp_output)
        self.assertEqual(violations, [])
        self.assertEqual(
            warnings,
            [
                "default: zg-newapi/ghost-model has unknown supportsTools metadata "
                "(resolved via omp models)",
            ],
        )

    def test_thinking_suffix_candidate_resolves_against_registration(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml({"bigctx": ["zg-newapi/gpt-5.6-sol:high"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])

    def test_duplicate_normalized_candidate_is_hard_violation(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml({"bigctx": ["zg-newapi/gpt-5.6-sol:high", "zg-newapi/gpt-5.6-sol"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(
            violations,
            ["bigctx: duplicate normalized candidate 'zg-newapi/gpt-5.6-sol'"],
        )
        self.assertEqual(warnings, [])

    def test_disabled_provider_candidate_is_hard_violation(self):
        models = _models_yml(
            _model_block("gpt-5", contextWindow=128000, maxTokens=4096),
            provider="openai",
        )
        config = _config_yml({"bigctx": ["openai/gpt-5"]}, disabled=["openai"])
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, ["bigctx: provider openai is globally disabled"])
        self.assertEqual(warnings, [])

    def test_supports_tools_false_in_tool_centric_chain_is_hard_violation(self):
        models = _models_yml(
            _model_block(
                "gpt-5.6-sol",
                supportsTools=False,
                contextWindow=1048576,
                maxTokens=128000,
            ),
        )
        config = _config_yml({"default": ["zg-newapi/gpt-5.6-sol"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(
            violations,
            [
                "default: zg-newapi/gpt-5.6-sol declares supportsTools: false (tool-centric chain)",
            ],
        )
        self.assertEqual(warnings, [])
        # 以 tool-centric 角色主模型的精确 selector 为链键，同样必须硬失败
        config = _config_yml(
            {"zg-newapi/gpt-5.6-sol": ["zg-newapi/gpt-5.6-sol"]},
            roles={"task": "zg-newapi/gpt-5.6-sol:high"},
        )
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(
            violations,
            [
                "zg-newapi/gpt-5.6-sol: zg-newapi/gpt-5.6-sol declares supportsTools: false "
                "(tool-centric chain)",
            ],
        )
        self.assertEqual(warnings, [])

    def test_supports_tools_false_in_non_tool_centric_chain_is_allowed(self):
        models = _models_yml(
            _model_block(
                "gpt-5.6-sol",
                supportsTools=False,
                contextWindow=1048576,
                maxTokens=128000,
            ),
        )
        config = _config_yml({"bigctx": ["zg-newapi/gpt-5.6-sol"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])

    def test_vision_chain_input_without_image_is_hard_violation(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", input=["text"], contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml({"vision": ["zg-newapi/gpt-5.6-sol"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(
            violations,
            ["vision: zg-newapi/gpt-5.6-sol declares input without image (vision chain)"],
        )
        self.assertEqual(warnings, [])

    def test_vision_chain_input_with_image_is_clean(self):
        models = _models_yml(
            _model_block(
                "gpt-5.6-sol",
                input=["text", "image"],
                contextWindow=1048576,
                maxTokens=128000,
            ),
        )
        config = _config_yml({"vision": ["zg-newapi/gpt-5.6-sol"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])

    def test_non_positive_context_window_or_max_tokens_is_hard_violation(self):
        for field, raw in (
            ("contextWindow", "0"),
            ("contextWindow", "-5"),
            ("contextWindow", "abc"),
            ("maxTokens", "0"),
            ("maxTokens", "-1"),
        ):
            with self.subTest(field=field, raw=raw):
                models = _models_yml(_model_block("gpt-5.6-sol", **{field: raw}))
                config = _config_yml({"bigctx": ["zg-newapi/gpt-5.6-sol"]})
                violations, warnings = validate_fallback_chains(config, models, "")
                self.assertEqual(len(violations), 1, violations)
                self.assertIn(
                    f"models.yml: zg-newapi/gpt-5.6-sol {field} must be positive",
                    violations[0],
                )
                self.assertEqual(warnings, [])

    def test_missing_capability_metadata_warns_but_does_not_fail(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml(
            {"default": ["zg-newapi/gpt-5.6-sol"], "vision": ["zg-newapi/gpt-5.6-sol"]},
        )
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, [])
        self.assertEqual(
            warnings,
            [
                "default: zg-newapi/gpt-5.6-sol has unknown supportsTools metadata "
                "(tool-centric chain)",
                "vision: zg-newapi/gpt-5.6-sol has unknown input modalities (vision chain)",
            ],
        )

    def test_unknown_metadata_values_warn_but_do_not_fail(self):
        models = _models_yml(
            _model_block(
                "gpt-5.6-sol",
                supportsTools="sometimes",
                input="text",
                contextWindow=1048576,
                maxTokens=128000,
            ),
        )
        config = _config_yml(
            {"default": ["zg-newapi/gpt-5.6-sol"], "vision": ["zg-newapi/gpt-5.6-sol"]},
        )
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, [])
        self.assertEqual(
            warnings,
            [
                "default: zg-newapi/gpt-5.6-sol has unknown supportsTools metadata "
                "(tool-centric chain)",
                "vision: zg-newapi/gpt-5.6-sol has unknown input modalities (vision chain)",
            ],
        )

    def test_wildcard_selector_is_valid_but_warns_when_not_fully_resolvable(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml({"bigctx": ["zg-newapi/*"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, [])
        self.assertEqual(
            warnings,
            ["bigctx: wildcard 'zg-newapi/*' cannot be resolved statically"],
        )

    def test_wildcard_with_unregistered_provider_warns_but_does_not_fail(self):
        models = _models_yml(
            _model_block("gpt-5.6-sol", contextWindow=1048576, maxTokens=128000),
        )
        config = _config_yml({"bigctx": ["ghost-provider/*"]})
        violations, warnings = validate_fallback_chains(config, models, "")
        self.assertEqual(violations, [])
        self.assertEqual(
            warnings,
            [
                "bigctx: wildcard 'ghost-provider/*' provider ghost-provider has no registered models",
            ],
        )

    def test_live_fallback_chains_have_no_hard_violations(self):
        """实时配置必须零硬违规；能力缺失只允许产生确定性警告。"""
        config_text = CONFIG_FILE.read_text(encoding="utf-8")
        models_text = MODELS_FILE.read_text(encoding="utf-8")
        violations, warnings = validate_fallback_chains(
            config_text, models_text, self.omp_models_output
        )
        report = ["fallback-chain gate warnings:"]
        report.extend(f"  - {warning}" for warning in warnings)
        report.append(f"fallback-chain gate total warnings: {len(warnings)}")
        self.assertEqual(
            violations,
            [],
            "hard fallback-chain violations:\n"
            + "\n".join(f"  - {violation}" for violation in violations)
            + "\n"
            + "\n".join(report),
        )

    def test_live_models_registrations_parse_expected_providers(self):
        parsed = _parse_model_registrations(MODELS_FILE.read_text(encoding="utf-8"))
        self.assertTrue(
            {"zg-newapi", "zg-newapi-anthropic", "agentrouter", "longcat", "anyrouter"}
            <= set(parsed),
            sorted(parsed),
        )
        zg_newapi_ids = {model["id"] for model in parsed["zg-newapi"]}
        self.assertIn("gpt-5.6-sol", zg_newapi_ids)
        qwen = next(model for model in parsed["zg-newapi"] if model["id"] == "qwen3.8-max")
        self.assertEqual(qwen["input"], ["text", "image"])
        self.assertNotIn("apiKey", repr(parsed))


if __name__ == "__main__":
    unittest.main()
