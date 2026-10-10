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
    if api == "anthropic":
        root = base.rstrip("/")
        url = (root + "/messages") if root.endswith("/v1") else (root + "/v1/messages")
        body = {"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": "Reply only: OK."}]}
        hdr = {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        url = base.rstrip("/") + "/chat/completions"
        body = {"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": "Reply only: OK."}]}
        if effort:
            body["reasoning"] = {"effort": effort}
        hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdr)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            j = json.loads(r.read())
        c = (j.get("choices") or [{}])[0].get("message", {}).get("content") or (j.get("content") or "")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        return r.status, round(time.time() - t0, 1), (c or "<empty content>")[:34]
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
        marked = "ok" if model in known else "NOT-IN-LIST"
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
