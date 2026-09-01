#!/usr/bin/env python3
"""capability_gov.py — capability registry governance (skills/MCP/plugins/tools).

Regenerates the actual installed inventory and diffs against
registry/capabilities.yaml + skills.json + mcp.json. Every drift is either
zero or must carry an explicit accepted status. Upstream discovery remains
proposal-only in upstream_capability_review.py; it never enters canonical here.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import HOME, REPO, gov_log, load_canonical, load_yaml  # noqa: E402


def main() -> int:
    canon = load_canonical()
    caps = canon["capabilities"]["capabilities"]
    std = caps["mcp_standard"]
    accepted_local = caps.get("harness_local", {})
    findings = []

    # skills: registered vs on-disk
    skills_reg = {s["path"].replace("./", "") for s in json.loads(
        (REPO / "skills.json").read_text(encoding="utf-8"))["skills"]}
    skills_disk = {str(p.relative_to(REPO)).replace("\\", "/")
                   for p in (REPO / "skills").iterdir() if (p / "SKILL.md").is_file()}
    for s in sorted(skills_reg - skills_disk):
        findings.append({"kind": "registered_but_missing", "layer": "skill", "name": s})
    for s in sorted(skills_disk - skills_reg):
        findings.append({"kind": "installed_but_unregistered", "layer": "skill", "name": s})

    # mcp: registered vs on-disk
    mcp_reg = {m["path"].replace("./", "") for m in json.loads(
        (REPO / "mcp.json").read_text(encoding="utf-8"))["mcp_servers"]}
    mcp_disk = {str(p.relative_to(REPO)).replace("\\", "/")
                for p in (REPO / "mcp").iterdir() if p.is_dir() and not p.name.startswith("__")}
    for s in sorted(mcp_reg - mcp_disk):
        findings.append({"kind": "registered_but_missing", "layer": "mcp", "name": s})
    for s in sorted(mcp_disk - mcp_reg):
        findings.append({"kind": "installed_but_unregistered", "layer": "mcp", "name": s})

    # harness MCP servers vs canonical capabilities (scope check)
    for harness, home, fname, key in (("codex", ".codex", "config.toml", "mcp_servers"),
                                      ("gemini", ".gemini", "settings.json", "mcpServers")):
        p = HOME / home / fname
        if not p.is_file():
            findings.append({"kind": "orphan", "layer": "harness", "name": harness,
                             "note": "config file missing"})
            continue
        data = (tomllib.loads(p.read_text(encoding="utf-8-sig")) if fname.endswith(".toml")
                else json.loads(p.read_text(encoding="utf-8-sig")))
        installed = set(data.get(key, {}).keys())
        for name in sorted(installed - set(std.keys())):
            acc = accepted_local.get(name)
            if acc and acc.get("status") == "accepted" and acc.get("harness_scope") == harness:
                continue  # explicit accepted status
            if acc and acc.get("harness_scope") != harness:
                findings.append({"kind": "wrong_harness_scope", "layer": f"mcp:{harness}",
                                 "name": name, "expected_scope": acc.get("harness_scope")})
            else:
                findings.append({"kind": "installed_but_unregistered", "layer": f"mcp:{harness}",
                                 "name": name})
        for name in sorted(set(std.keys()) - installed):
            scope = std[name].get("harness_scope", "all")
            if scope in ("all", harness):
                findings.append({"kind": "registered_but_missing", "layer": f"mcp:{harness}",
                                 "name": name})

    for f in findings:
        print(f"DRIFT: {f['kind']} {f['layer']} {f['name']}")
    print(f"CAPABILITY_DRIFT = {len(findings)}")
    gov_log("capability_gov", "ok" if not findings else "findings", findings)
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
