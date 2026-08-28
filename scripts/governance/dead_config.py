#!/usr/bin/env python3
"""dead_config.py — dead configuration detection with combined evidence.

ACTIVE / INACTIVE_KNOWN / DEAD_CANDIDATE / BROKEN / UNKNOWN. Evidence:
declared, installed, referenced, observed_usage, runtime_reachable, owner.
Output = REMOVE_PROPOSAL at most. Never deletes.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import HOME, REPO, gov_log, load_canonical, private_gateways, propose  # noqa: E402


def reachable(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    canon = load_canonical()
    pgw = private_gateways().get("gateways", {})
    models = canon["models"]["models"]
    rules = canon["policy"]["rules"]
    referenced = {rules[k].get("model") for k in
                  ("main_default", "subagent_spawn", "subagent_fork", "compaction_summary")}
    referenced |= {m for v in rules.get("fallbacks", {}).values() for m in v}

    findings = []
    print("=== providers ===")
    for name, pdef in canon["providers"]["providers"].items():
        url = f"http://{pgw[name]['listen']}/v1/models" if name in pgw and pgw[name].get("listen") else None
        reach = reachable(url) if url else None
        state = "ACTIVE" if reach else ("BROKEN" if reach is False else "UNKNOWN")
        print(f"  {name}: declared=yes reachable={reach} -> {state}")
        if state == "BROKEN":
            findings.append({"kind": "provider_unreachable", "provider": name, "state": state})
            propose("dead_config", {"item": f"provider:{name}", "evidence": "unreachable",
                                    "declared": True, "referenced": True},
                    "medium", "registry/providers.yaml", "REMOVE_PROPOSAL or repair")
    print("=== models ===")
    for m in models:
        mid = m["id"]
        ref = mid in referenced
        roles = m.get("roles", [])
        state = "ACTIVE" if (ref or roles) else "INACTIVE_KNOWN"
        print(f"  {mid}: referenced={ref} roles={roles} -> {state}")
        if not ref and not roles:
            findings.append({"kind": "model_unreferenced", "model": mid, "state": state})
    print("=== harness mcp servers vs capabilities ===")
    std = set(canon["capabilities"]["capabilities"]["mcp_standard"].keys())
    for harness, home, fname, key in (("codex", ".codex", "config.toml", "mcp_servers"),
                                      ("gemini", ".gemini", "settings.json", "mcpServers")):
        p = HOME / home / fname
        if not p.is_file():
            continue
        if fname.endswith(".toml"):
            import tomllib
            data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
        else:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        installed = set(data.get(key, {}).keys())
        missing = std - installed
        extra = installed - std
        if missing:
            findings.append({"kind": "capability_missing", "harness": harness,
                             "missing": sorted(missing)})
        if extra:
            findings.append({"kind": "capability_unregistered", "harness": harness,
                             "extra": sorted(extra), "state": "UNKNOWN"})
        print(f"  {harness}: installed={sorted(installed)} missing={sorted(missing)} extra={sorted(extra)}")

    gov_log("dead_config", "ok" if not findings else "findings", findings)
    print(f"findings={len(findings)}")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
