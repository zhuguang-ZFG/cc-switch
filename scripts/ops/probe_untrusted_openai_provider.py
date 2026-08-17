#!/usr/bin/env python3
"""Bounded, redacted conformance canary for an untrusted OpenAI-compatible API."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_BASE_URL = "https://www.sotamodel.net"
DEFAULT_KEY_ENV = "SOTAMODEL_API_KEY"
DEFAULT_CANDIDATES = (
    "claude-opus-5",
    "claude-opus-5-max",
    "claude-opus-5-xhigh",
    "gpt-5.6-sol",
    "gpt-5.6-sol-max",
    "gpt-5.6-sol-xhigh",
    "model-A",
    "model-T",
    "model-O",
    "model-S",
)
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_CATALOG_MODELS = 200
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class HttpResult:
    status: int
    content_type: str
    body: bytes
    elapsed_ms: int
    truncated: bool = False


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme != "https":
        raise ValueError("base URL must use https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("base URL must have a host and no userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain query or fragment data")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def validate_model_id(value: str) -> str:
    if not MODEL_ID_PATTERN.fullmatch(value):
        raise ValueError(f"invalid model id: {value!r}")
    return value


def safe_response_model(value: Any) -> str | None:
    if isinstance(value, str) and MODEL_ID_PATTERN.fullmatch(value):
        return value
    return "[invalid-model-id]" if isinstance(value, str) else None


def endpoint(base_url: str, path: str) -> str:
    return f"{base_url}/v1/{path.lstrip('/')}"


def request(
    url: str,
    key: str,
    *,
    timeout: float,
    payload: dict[str, Any] | None = None,
    accept: str = "application/json",
) -> HttpResult:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": accept,
        "User-Agent": "cc-switch-untrusted-provider-canary/1",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=data,
        method="GET" if data is None else "POST",
        headers=headers,
    )
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(MAX_BODY_BYTES + 1)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return HttpResult(
            status=int(response.status),
            content_type=response.headers.get("Content-Type", ""),
            body=body[:MAX_BODY_BYTES],
            elapsed_ms=elapsed_ms,
            truncated=len(body) > MAX_BODY_BYTES,
        )


def int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def usage_summary(payload: Any) -> dict[str, int | bool]:
    usage = payload if isinstance(payload, dict) else {}
    prompt_details = usage.get("prompt_tokens_details")
    input_details = usage.get("input_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    input_details = input_details if isinstance(input_details, dict) else {}
    prompt_tokens = int_value(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = int_value(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    cache_read = max(
        int_value(usage.get("cache_read_input_tokens")),
        int_value(prompt_details.get("cached_tokens")),
        int_value(input_details.get("cached_tokens")),
    )
    cache_creation = int_value(usage.get("cache_creation_input_tokens"))
    return {
        "present": bool(usage),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
    }


def empty_usage() -> dict[str, int | bool]:
    return usage_summary({})


def merge_usage(current: dict[str, int | bool], candidate: dict[str, int | bool]) -> None:
    current["present"] = bool(current["present"] or candidate["present"])
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    ):
        current[key] = max(int(current[key]), int(candidate[key]))


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def parse_json_response(body: bytes) -> dict[str, Any]:
    if not body.strip():
        return {
            "wire_format": "empty",
            "response_model": None,
            "text": "",
            "done": False,
            "usage": empty_usage(),
            "tool_calls": [],
            "parse_error": None,
        }
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "wire_format": "invalid-json",
            "response_model": None,
            "text": "",
            "done": False,
            "usage": empty_usage(),
            "tool_calls": [],
            "parse_error": type(exc).__name__,
        }
    if not isinstance(payload, dict):
        return {
            "wire_format": "json",
            "response_model": None,
            "text": "",
            "done": False,
            "usage": empty_usage(),
            "tool_calls": [],
            "parse_error": "non-object-json",
        }
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    text = content_text(message.get("content"))
    if not text:
        text = content_text(payload.get("output_text"))
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        tool_calls = []
    usage = payload.get("usage")
    response = payload.get("response")
    if not isinstance(usage, dict) and isinstance(response, dict):
        usage = response.get("usage")
    return {
        "wire_format": "json",
        "response_model": safe_response_model(payload.get("model")),
        "text": text,
        "done": True,
        "usage": usage_summary(usage),
        "tool_calls": tool_calls,
        "parse_error": None,
    }


def parse_sse_response(body: bytes) -> dict[str, Any]:
    fragments: list[str] = []
    response_model: str | None = None
    done = False
    usage = empty_usage()
    tool_fragments: dict[int, dict[str, str]] = {}
    parse_errors = 0
    event_count = 0
    for raw_line in body.decode("utf-8", errors="replace").splitlines():
        if not raw_line.startswith("data:"):
            continue
        data = raw_line[5:].strip()
        if not data:
            continue
        event_count += 1
        if data == "[DONE]":
            done = True
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("model"), str):
            response_model = safe_response_model(event["model"])
        choices = event.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        fragments.append(content_text(delta.get("content")))
        fragments.append(content_text(message.get("content")))
        if event.get("type") == "response.output_text.delta" and isinstance(event.get("delta"), str):
            fragments.append(event["delta"])
        tool_calls = delta.get("tool_calls") or message.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue
                index = int_value(item.get("index"))
                function = item.get("function") if isinstance(item.get("function"), dict) else {}
                record = tool_fragments.setdefault(index, {"name": "", "arguments": ""})
                if isinstance(function.get("name"), str):
                    record["name"] += function["name"]
                if isinstance(function.get("arguments"), str):
                    record["arguments"] += function["arguments"]
        event_usage = event.get("usage")
        response = event.get("response")
        if not isinstance(event_usage, dict) and isinstance(response, dict):
            event_usage = response.get("usage")
            if response_model is None and isinstance(response.get("model"), str):
                response_model = safe_response_model(response["model"])
        merge_usage(usage, usage_summary(event_usage))
    return {
        "wire_format": "sse",
        "response_model": response_model,
        "text": "".join(fragments),
        "done": done,
        "usage": usage,
        "tool_calls": [tool_fragments[key] for key in sorted(tool_fragments)],
        "parse_error": f"invalid-sse-events:{parse_errors}" if parse_errors else None,
        "event_count": event_count,
    }


def parse_response(result: HttpResult) -> dict[str, Any]:
    if "text/event-stream" in result.content_type.lower() or result.body.lstrip().startswith(b"data:"):
        return parse_sse_response(result.body)
    return parse_json_response(result.body)


def probe_chat(
    base_url: str,
    key: str,
    model: str,
    *,
    stream: bool,
    timeout: float,
    marker: str,
    tools: bool = False,
    prompt: str | None = None,
) -> dict[str, Any]:
    messages = [{"role": "user", "content": prompt or f"Reply with exactly: {marker}"}]
    payload: dict[str, Any] = {
        "model": model,
        "stream": stream,
        "max_tokens": 32,
        "temperature": 0,
        "messages": messages,
    }
    expected_tool = None
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if tools:
        expected_tool = "report_canary"
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": expected_tool,
                    "description": "Record the fixed canary value.",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        payload["tool_choice"] = {"type": "function", "function": {"name": expected_tool}}
        payload["messages"] = [
            {"role": "user", "content": "Call report_canary with value CANARY_TOOL_OK."}
        ]
    result = request(
        endpoint(base_url, "chat/completions"),
        key,
        timeout=timeout,
        payload=payload,
        accept="text/event-stream" if stream else "application/json",
    )
    parsed = parse_response(result)
    response_model = parsed["response_model"]
    tool_names: list[str] = []
    tool_arguments_valid = False
    for item in parsed["tool_calls"]:
        function = item.get("function") if isinstance(item, dict) else None
        if isinstance(function, dict):
            name = function.get("name")
            arguments = function.get("arguments")
        else:
            name = item.get("name") if isinstance(item, dict) else None
            arguments = item.get("arguments") if isinstance(item, dict) else None
        if isinstance(name, str):
            tool_names.append(name)
        if isinstance(arguments, str):
            try:
                decoded = json.loads(arguments)
                tool_arguments_valid = decoded == {"value": "CANARY_TOOL_OK"}
            except json.JSONDecodeError:
                pass
    issues: list[str] = []
    if result.truncated:
        issues.append("response-body-limit-exceeded")
    if result.status != 200:
        issues.append(f"http-{result.status}")
    if parsed["parse_error"]:
        issues.append(parsed["parse_error"])
    if not stream and parsed["wire_format"] == "sse":
        issues.append("stream-false-returned-sse")
    if parsed["wire_format"] == "empty" or (not parsed["text"] and not parsed["tool_calls"]):
        issues.append("empty-semantic-output")
    if stream and not parsed["done"]:
        issues.append("stream-missing-done")
    if not parsed["usage"]["present"]:
        issues.append("usage-missing")
    if response_model and response_model != model:
        issues.append("response-model-mismatch")
    if tools and expected_tool not in tool_names:
        issues.append("required-tool-call-missing")
    if tools and expected_tool in tool_names and not tool_arguments_valid:
        issues.append("tool-arguments-invalid")
    return {
        "kind": "tool" if tools else ("stream" if stream else "nonstream"),
        "requested_model": model,
        "http_status": result.status,
        "content_type": result.content_type.split(";", 1)[0].strip().lower(),
        "wire_format": parsed["wire_format"],
        "response_model": response_model,
        "semantic_match": parsed["text"].strip() == marker if not tools else None,
        "done": parsed["done"],
        "usage": parsed["usage"],
        "tool_call_valid": expected_tool in tool_names and tool_arguments_valid if tools else None,
        "elapsed_ms": result.elapsed_ms,
        "issues": sorted(set(issues)),
    }


def fetch_catalog(base_url: str, key: str, timeout: float) -> dict[str, Any]:
    result = request(endpoint(base_url, "models"), key, timeout=timeout)
    model_ids: list[str] = []
    rejected_model_ids = 0
    parse_error = None
    if result.status == 200 and not result.truncated:
        try:
            payload = json.loads(result.body)
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                candidates = [
                    item["id"]
                    for item in data
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
                rejected_model_ids = sum(
                    not bool(MODEL_ID_PATTERN.fullmatch(candidate))
                    for candidate in candidates
                )
                model_ids = sorted(
                    {
                        candidate
                        for candidate in candidates
                        if MODEL_ID_PATTERN.fullmatch(candidate)
                    }
                )[:MAX_CATALOG_MODELS]
            else:
                parse_error = "catalog-data-missing"
        except (UnicodeDecodeError, json.JSONDecodeError):
            parse_error = "catalog-invalid-json"
    return {
        "http_status": result.status,
        "content_type": result.content_type.split(";", 1)[0].strip().lower(),
        "elapsed_ms": result.elapsed_ms,
        "truncated": result.truncated,
        "model_count": len(model_ids),
        "model_ids": model_ids,
        "rejected_model_ids": rejected_model_ids,
        "parse_error": parse_error,
    }


def cache_probe(base_url: str, key: str, model: str, timeout: float) -> dict[str, Any]:
    nonce = secrets.token_hex(12)
    marker = f"CACHE_{nonce}"
    prompt = (f"Unique cache canary {nonce}. " + "stable-prefix " * 300) + f" Reply exactly: {marker}"
    first = probe_chat(
        base_url, key, model, stream=True, timeout=timeout, marker=marker, prompt=prompt
    )
    second = probe_chat(
        base_url, key, model, stream=True, timeout=timeout, marker=marker, prompt=prompt
    )
    first_read = int(first["usage"]["cache_read_tokens"])
    second_read = int(second["usage"]["cache_read_tokens"])
    return {
        "model": model,
        "first_http_status": first["http_status"],
        "second_http_status": second["http_status"],
        "first_semantic_match": first["semantic_match"],
        "second_semantic_match": second["semantic_match"],
        "first_cache_read_tokens": first_read,
        "second_cache_read_tokens": second_read,
        "suspicious_first_request_cache_hit": first_read > 0,
        "repeat_cache_observed": second_read > first_read,
        "usage_present": bool(first["usage"]["present"] and second["usage"]["present"]),
        "elapsed_ms": first["elapsed_ms"] + second["elapsed_ms"],
        "issues": sorted(set(first["issues"] + second["issues"])),
    }


def alias_collapses(probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_response: dict[str, set[str]] = {}
    for probe in probes:
        if probe.get("kind") != "stream" or probe.get("http_status") != 200:
            continue
        requested = probe.get("requested_model")
        response = probe.get("response_model")
        if isinstance(requested, str) and isinstance(response, str):
            by_response.setdefault(response, set()).add(requested)
    return [
        {"response_model": response, "requested_models": sorted(requested)}
        for response, requested in sorted(by_response.items())
        if len(requested) > 1 or next(iter(requested)) != response
    ]


def wire_model_variants(probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_request: dict[str, dict[str, str]] = {}
    for probe in probes:
        if probe.get("kind") not in {"nonstream", "stream"}:
            continue
        requested = probe.get("requested_model")
        response = probe.get("response_model")
        kind = probe.get("kind")
        if isinstance(requested, str) and isinstance(response, str) and isinstance(kind, str):
            by_request.setdefault(requested, {})[kind] = response
    return [
        {"requested_model": requested, "response_models_by_wire": dict(sorted(wires.items()))}
        for requested, wires in sorted(by_request.items())
        if len(set(wires.values())) > 1
    ]


def selected_models(explicit: str | None, catalog: list[str], max_models: int) -> list[str]:
    candidates = (
        [part.strip() for part in explicit.split(",") if part.strip()]
        if explicit
        else list(DEFAULT_CANDIDATES)
    )
    candidates = [validate_model_id(candidate) for candidate in candidates]
    if catalog:
        available = set(catalog)
        candidates = [candidate for candidate in candidates if candidate in available]
    return list(dict.fromkeys(candidates))[:max_models]


def safe_json(report: dict[str, Any], secret: str | None = None) -> str:
    def has_authorization_key(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                str(key).lower() in {"authorization", "proxy-authorization"}
                or has_authorization_key(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(has_authorization_key(item) for item in value)
        return False

    if has_authorization_key(report):
        raise RuntimeError("authorization metadata reached the report boundary")
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if secret and secret in text:
        raise RuntimeError("secret reached the report boundary")
    return text


def dry_run_report(args: argparse.Namespace, base_url: str) -> dict[str, Any]:
    models = selected_models(args.models, [], args.max_models)
    return {
        "mode": "dry-run",
        "base_host": urllib.parse.urlsplit(base_url).hostname,
        "key_env": args.api_key_env,
        "key_present": bool(os.environ.get(args.api_key_env)),
        "selected_models": models,
        "planned_requests": 1 + 2 * len(models) + (0 if args.skip_tool else 1) + (0 if args.skip_cache else 2),
        "safety": {
            "serial": True,
            "timeout_seconds": args.timeout,
            "max_body_bytes": MAX_BODY_BYTES,
            "stores_bodies": False,
            "mutates_provider": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="perform the bounded network probes")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default=DEFAULT_KEY_ENV)
    parser.add_argument("--models", help="comma-separated model ids; defaults to known alias candidates")
    parser.add_argument("--max-models", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--tool-model", help="model for the one required-tool probe")
    parser.add_argument("--cache-model", help="model for the two-request cache probe")
    parser.add_argument("--skip-tool", action="store_true")
    parser.add_argument("--skip-cache", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.max_models <= 12:
        parser.error("--max-models must be between 1 and 12")
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    try:
        base_url = validate_base_url(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.run:
        print(safe_json(dry_run_report(args, base_url)))
        return 0

    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        print(f"missing API key environment variable: {args.api_key_env}", file=sys.stderr)
        return 2
    try:
        catalog = fetch_catalog(base_url, key, args.timeout)
        models = selected_models(args.models, catalog["model_ids"], args.max_models)
        if not models:
            raise RuntimeError("none of the requested canary models are in the catalog")
        probes: list[dict[str, Any]] = []
        for model in models:
            marker = f"CANARY_{secrets.token_hex(8)}"
            probes.append(
                probe_chat(
                    base_url, key, model, stream=False, timeout=args.timeout, marker=marker
                )
            )
            probes.append(
                probe_chat(
                    base_url, key, model, stream=True, timeout=args.timeout, marker=marker
                )
            )
        tool_model = args.tool_model or models[0]
        tool_result = None
        if not args.skip_tool:
            tool_result = probe_chat(
                base_url,
                key,
                tool_model,
                stream=True,
                timeout=args.timeout,
                marker="",
                tools=True,
            )
        cache_result = None
        if not args.skip_cache:
            cache_result = cache_probe(
                base_url, key, args.cache_model or models[0], args.timeout
            )
        issue_counts: dict[str, int] = {}
        for probe in probes + ([tool_result] if tool_result else []):
            for issue in probe["issues"]:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        report = {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "live-read-only",
            "base_host": urllib.parse.urlsplit(base_url).hostname,
            "key_env": args.api_key_env,
            "catalog": catalog,
            "selected_models": models,
            "probes": probes,
            "tool_probe": tool_result,
            "cache_probe": cache_result,
            "alias_collapses": alias_collapses(probes),
            "wire_model_variants": wire_model_variants(probes),
            "summary": {
                "requests_sent": 1 + len(probes) + (1 if tool_result else 0) + (2 if cache_result else 0),
                "issue_counts": dict(sorted(issue_counts.items())),
                "production_eligible": False,
                "recommended_scope": "manual-canary-only",
            },
        }
        print(safe_json(report, key))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        message = str(exc).replace(key, "[redacted]")
        print(f"canary failed: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
