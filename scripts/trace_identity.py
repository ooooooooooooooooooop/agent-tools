#!/usr/bin/env python3
"""trace_identity.py — project a DSH session log into model-identity-trace/v1 answers.

Frozen contract (registry/model-identity-trace.schema.yaml):
requested_model -> gateway_resolved_model -> provider_reported_model -> identity_assessment
status in {consistent, suspicious, unknown}. No verified_model / verification_confidence.

Usage: python scripts/trace_identity.py <session.jsonl.zstd|session.jsonl>
Read-only; offline; no LLM calls.
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RETRY_RE = re.compile(r"retry|fallback", re.IGNORECASE)


def iter_events(path: Path):
    if path.suffix == ".zstd":
        import zstandard as zstd

        with path.open("rb") as fh:
            with zstd.ZstdDecompressor().stream_reader(fh) as reader:
                for line in io.TextIOWrapper(reader, encoding="utf-8", errors="replace"):
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
    else:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue



def _attribute_policy_rule(requested: dict | None, rules: dict | None) -> str:
    """Attribute policy rule by comparing requested model against routing-policy (honest: unknown if no match)."""
    if not requested or not rules:
        return "unknown (no routing-policy reference available)"
    want = f"{requested.get('provider')}/{requested.get('model')}"
    for name, target in rules.items():
        if target == want:
            return name
    return "unknown (requested model not referenced by canonical routing-policy)"
def project(path: Path, gateways: dict) -> dict:
    requested = None
    reported: dict = {}
    fallback_events = []
    for ev in iter_events(path):
        data = ev.get("data", ev)
        header = data.get("header") if isinstance(data, dict) else None
        if isinstance(header, dict) and isinstance(header.get("config"), dict):
            cfg = header["config"]
            if cfg.get("provider") or cfg.get("model"):
                requested = {"provider": cfg.get("provider"), "model": cfg.get("model")}
        msg = data.get("message") if isinstance(data, dict) else None
        src = msg.get("source") if isinstance(msg, dict) else None
        if isinstance(src, dict) and (src.get("provider") or src.get("model")):
            key = (src.get("provider"), src.get("model"))
            reported[key] = reported.get(key, 0) + 1
        blob = json.dumps(ev, ensure_ascii=False)
        if ev.get("type", "").startswith(("llm/", "system/")) and RETRY_RE.search(blob):
            fallback_events.append(ev.get("type"))

    resolved = requested
    if requested:
        for gw in gateways.get("gateways", {}).values():
            amap = gw.get("alias_map") or {}
            if requested.get("model") in amap:
                resolved = {"provider": requested.get("provider"),
                            "model": amap[requested["model"]]}
                break

    if not reported:
        status = "unknown"
        reported_main = None
    else:
        reported_main = max(reported.items(), key=lambda kv: kv[1])
        (rprov, rmodel), _count = reported_main
        if requested and rprov == requested.get("provider") and rmodel == resolved.get("model"):
            status = "consistent"
        else:
            status = "suspicious"
        reported_main = {"provider": rprov, "model": rmodel, "messages": _count}

    return {
        "schema": "model-identity-trace/v1",
        "session": str(path),
        "requested_model": requested,
        "gateway_resolved_model": resolved,
        "provider_reported_model": reported_main,
        "provider_reported_all": [{"provider": k[0], "model": k[1], "messages": v}
                                   for k, v in sorted(reported.items())],
        "identity_assessment": {"status": status,
                                 "signals": ["session-log-projection"]},
        "fallback": {"observed_events": len(fallback_events),
                      "note": "none-observed" if not fallback_events else "see session log"},
        "policy_rule": _attribute_policy_rule(requested, gateways.get("_policy_rules")),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    gateways = {}
    candidates = [Path.home() / "personal-ai-state" / "registry" / "gateways.yaml",
                  ROOT / "registry" / "gateways.yaml"]
    for gw_path in candidates:
        if gw_path.is_file():
            gateways = yaml.safe_load(gw_path.read_text(encoding="utf-8-sig"))
            break
    rp = ROOT / "registry" / "routing-policy.yaml"
    if rp.is_file():
        pol = yaml.safe_load(rp.read_text(encoding="utf-8-sig")) or {}
        gateways["_policy_rules"] = {
            name: f"{r.get('provider')}/{r.get('model')}"
            for name, r in (pol.get("rules") or {}).items()
            if isinstance(r, dict) and r.get("provider")
        }
    result = project(Path(sys.argv[1]), gateways)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
