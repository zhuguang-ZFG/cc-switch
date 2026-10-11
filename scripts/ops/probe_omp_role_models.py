#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe each OMP modelRoles selector through the provider's own baseUrl.

Reads ~/.omp/agent/{config.yml,models.yml}, sends one tiny completion per role primary,
and prints status + first error line. Never prints keys. Used to answer "OMP 连不上模型"
without guessing which of the roles/pools is actually down.
"""
import json
import pathlib
import time
import urllib.error
import urllib.request

import yaml

AGENT = pathlib.Path.home() / ".omp" / "agent"


def provider_index(models):
    out = {}
    for name, prov in (models.get("providers") or {}).items():
        out[name] = prov
    return out


def find_models(prov):
    res = {}
    for m in prov.get("models") or []:
        if isinstance(m, dict) and m.get("id"):
            res[m["id"]] = m
    return res


def call(base, key, model, effort, api):
    root = base.rstrip("/")
    # The provider's api field is "anthropic-messages", not "anthropic" — a bare equality
    # test silently routed every anthropic role down the OpenAI branch.
    if "anthropic" in (api or ""):
        url = (root + "/messages") if root.endswith("/v1") else (root + "/v1/messages")
        body = {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "Reply only: OK."}]}
        hdr = {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        if not root.endswith("/v1"):
            root += "/v1"
        url = root + "/chat/completions"
        body = {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "Reply only: OK."}]}
        if effort:
            body["reasoning"] = {"effort": effort}
        hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdr)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw, status, ctype = r.read(), r.status, r.headers.get("content-type")
        dt = round(time.time() - t0, 1)
        try:
            j = json.loads(raw)
        except Exception:
            # 200 + HTML is the SPA catch-all, i.e. a wrong endpoint, not a model failure.
            return status, dt, f"NOT-JSON ctype={ctype} url={url} head={raw[:60]!r}"
        if isinstance(j.get("error"), dict):
            return status, dt, "upstream: " + str(j["error"].get("message"))[:120]
        ch = (j.get("choices") or [{}])[0]
        c = (ch.get("message") or {}).get("content") or j.get("content") or ""
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        det = ((j.get("usage") or {}).get("completion_tokens_details") or {})
        note = f" finish={ch.get('finish_reason')}"
        if not c and det.get("reasoning_tokens"):
            # empty content with a reasoning budget spent is truncation, not an outage.
            note += f" reasoning_tokens={det['reasoning_tokens']}"
        return status, dt, ((c or "<empty>")[:34] + note)
    except urllib.error.HTTPError as e:
        return e.code, round(time.time() - t0, 1), e.read().decode("utf-8", "replace").replace("\n", " ")[:150]
    except Exception as e:
        return "EXC", round(time.time() - t0, 1), f"{type(e).__name__}: {e}"[:150]


def main():
    cfg = yaml.safe_load((AGENT / "config.yml").read_text(encoding="utf-8"))
    models = yaml.safe_load((AGENT / "models.yml").read_text(encoding="utf-8"))
    provs = provider_index(models)
    roles = cfg.get("modelRoles") or {}
    for role, sel in sorted(roles.items()):
        sel = sel if isinstance(sel, str) else str(sel)
        prov_name, _, rest = sel.partition("/")
        model, _, effort = rest.partition(":")
        prov = provs.get(prov_name)
        if prov is None:
            print(f"{role:<9} {sel:<44} UNKNOWN PROVIDER (not in models.yml)")
            continue
        known = find_models(prov)
        # Declared-in-provider-list is NOT health; naming it "ok" invited exactly that misread.
        marked = "declared" if model in known else "NOT-IN-LIST"
        base = prov.get("baseUrl")
        key = prov.get("apiKey") or prov.get("api_key") or ""
        api = prov.get("api") or "openai-completions"
        if not isinstance(base, str) or not key:
            print(f"{role:<9} {sel:<44} provider={prov_name} missing baseUrl/key")
            continue
        st, sec, info = call(base, key, model, effort, api)
        print(f"{role:<9} {sel:<44} {str(st):>5} {sec:>6}s [{marked}] {info}")


if __name__ == "__main__":
    main()
