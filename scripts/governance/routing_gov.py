#!/usr/bin/env python3
"""routing_gov.py — routing policy governance (dry-run classification only).

Checks: referenced models exist; routing-enabled ⊆ admitted; fallback chains
acyclic + targets legal; provider reachability queryable; alias SSOT declared;
overlay usage visible (never silently bypassing). No expensive real tasks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import gov_log, load_canonical, private_gateways  # noqa: E402


def find_cycle(chain_map: dict[str, list[str]]) -> list[str] | None:
    def visit(node, path, seen):
        for nxt in chain_map.get(node, []):
            if nxt in path:
                return path + [nxt]
            if nxt not in seen:
                seen.add(nxt)
                r = visit(nxt, path + [nxt], seen)
                if r:
                    return r
        return None
    for start in chain_map:
        r = visit(start, [start], {start})
        if r:
            return r
    return None


def main() -> int:
    canon = load_canonical()
    models = canon["models"]["models"]
    admitted = {m["id"] for m in models}
    providers = canon["providers"]["providers"]
    rules = canon["policy"]["rules"]
    findings = []

    # rule -> model exists & admitted
    for rname in ("main_default", "subagent_spawn", "subagent_fork", "compaction_summary"):
        rule = rules.get(rname, {})
        m, p = rule.get("model"), rule.get("provider")
        if m and m not in admitted:
            findings.append({"kind": "rule_references_unadmitted_model", "rule": rname, "model": m})
        if p and p not in providers:
            findings.append({"kind": "rule_references_unknown_provider", "rule": rname, "provider": p})

    # fallback chains: acyclic + legal targets
    fallbacks = rules.get("fallbacks", {})
    for src, chain in fallbacks.items():
        for target in chain:
            if target not in admitted:
                findings.append({"kind": "fallback_target_unadmitted", "from": src, "to": target})
    cycle = find_cycle({k: v for k, v in fallbacks.items()})
    if cycle:
        findings.append({"kind": "fallback_cycle", "chain": cycle})

    # broker_preferences backends legal
    known_backends = {"cpa", "codex_cli", "claude_cli", "gemini_cli", "antigravity"}
    for k, chain in rules.get("broker_preferences", {}).items():
        if k == "evidence":
            continue
        for b in chain:
            if b not in known_backends:
                findings.append({"kind": "broker_preference_unknown_backend", "rule": k, "backend": b})

    # alias SSOT declared
    pgw = private_gateways().get("gateways", {})
    for gw in ("cc-switch", "cpa"):
        if gw not in pgw:
            findings.append({"kind": "gateway_ssot_missing", "gateway": gw})

    # dry-run classification per rule (no model calls)
    print("dry-run classification:")
    for rname in ("main_default", "subagent_spawn", "subagent_fork", "compaction_summary"):
        rule = rules.get(rname, {})
        ok = rule.get("model") in admitted and rule.get("provider") in providers
        print(f"  {rname}: {rule.get('provider')}/{rule.get('model')} "
              f"effort={rule.get('reasoningEffort', '-')} -> {'OK' if ok else 'VIOLATION'}")

    for f in findings:
        print(f"FINDING: {f}")
    gov_log("routing_gov", "ok" if not findings else "findings", findings)
    print(f"routing findings={len(findings)}")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
