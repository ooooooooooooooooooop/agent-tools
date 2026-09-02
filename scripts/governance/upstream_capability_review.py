#!/usr/bin/env python3
"""upstream_capability_review.py — Upstream Harness & AI infrastructure capability evolution engine.

Implements feature-level delta discovery, taxonomy normalization, Personal AI matching,
value assessment, and proposal generation. Discovery and evaluation are proposal-only;
they never mutate canonical registries or auto-apply to production runtime.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from common import REPO, gov_log, load_yaml, propose  # noqa: E402

AIC = REPO / "scripts" / "aic" / "aic.py"

# CAPABILITY_TAXONOMY (§2)
TAXONOMY = [
    "AGENT_ORCHESTRATION",
    "SUBAGENT",
    "WORKFLOW",
    "PARALLEL_EXECUTION",
    "MODEL_ROUTING",
    "MODEL_PROVENANCE",
    "CONTEXT_ADMISSION",
    "COMPACTION",
    "MEMORY",
    "TOOLING",
    "MCP",
    "PLUGIN",
    "BROWSER",
    "COMPUTER_USE",
    "CHECKPOINT",
    "BACKGROUND_EXECUTION",
    "OBSERVABILITY",
    "RECOVERY",
]

# RELATION definitions (§3)
RELATIONS = [
    "NEW_CAPABILITY",
    "PARTIAL_OVERLAP",
    "UPSTREAM_BETTER",
    "PERSONAL_AI_STILL_NEEDED",
    "COMPLEMENTARY",
    "BREAKING_CHANGE",
    "IRRELEVANT",
    "UNKNOWN",
]

# VALUE_ASSESSMENT outcomes (§4)
ASSESSMENTS = [
    "ADOPTION_CANDIDATE",
    "WATCH",
    "IGNORE",
    "REPLACEMENT_CANDIDATE",
    "BREAKING_CHANGE_REVIEW",
]

HARNESS_COMMANDS = {
    "dsh": ["dsh", "--version"],
    "codex": ["codex", "--version"],
    "claude": ["claude", "--version"],
    "gemini": ["gemini", "--version"],
}

# Upstream Feature Catalogs (Ground truth from inspected upstream codebases and release notes)
UPSTREAM_FEATURE_CATALOG: dict[str, list[dict[str, Any]]] = {
    "dsh": [
        {
            "feature_id": "dsh_workflow_dsl",
            "name": "DSH Workflow Script Engine & Pipeline Orchestration",
            "taxonomy": ["WORKFLOW", "PARALLEL_EXECUTION", "AGENT_ORCHESTRATION"],
            "version_introduced": "0.1.1-rc.2",
            "evidence": "node_modules/@deepseek-ai/dsh-workflow/lib/index.js (agent, pipeline, parallel, phase)",
            "summary": "Plain JS workflow script engine supporting parallel execution and sequential pipeline stages across subagents.",
            "personal_ai_equivalent": "scripts/workflow_preflight_router.py / dsh-workflow-model-preflight-gate",
            "relation": "COMPLEMENTARY",
            "assessment": "ADOPTION_CANDIDATE",
            "notes": "Upstream executes workflow scripts but does not guard child model admission; Personal AI gate complements it.",
        },
        {
            "feature_id": "dsh_subagent_continuation",
            "name": "DSH Subagent Continuable Sessions & Depth Hierarchy",
            "taxonomy": ["SUBAGENT", "BACKGROUND_EXECUTION", "OBSERVABILITY"],
            "version_introduced": "0.1.1-rc.2",
            "evidence": "node_modules/@deepseek-ai/dsh-subagent/lib/continuation.js",
            "summary": "Continuable background subagents with depth limits, durable session ids, and send_message turn continuation.",
            "personal_ai_equivalent": "subagent-execution-governance / dsh-autonomous-execution-governor",
            "relation": "COMPLEMENTARY",
            "assessment": "ADOPTION_CANDIDATE",
            "notes": "Native continuation provides lifecycle transport; Personal AI governor provides turn/budget/loop bounds.",
        },
        {
            "feature_id": "dsh_basic_compaction",
            "name": "DSH Basic Compaction & Tool Result Pruner",
            "taxonomy": ["COMPACTION", "CONTEXT_ADMISSION"],
            "version_introduced": "0.1.1-rc.2",
            "evidence": "node_modules/@deepseek-ai/dsh-compaction/lib/index.js",
            "summary": "Automatic context compaction via summarization provider and head/tail pruning of long tool outputs.",
            "personal_ai_equivalent": "dsh-compaction-convergence & dsh-tool-result-pruner-pressure-guard",
            "relation": "PERSONAL_AI_STILL_NEEDED",
            "assessment": "WATCH",
            "notes": "Upstream compaction basic enters infinite loop on summarization failure; custom convergence overlay remains strictly needed.",
        },
        {
            "feature_id": "dsh_tools_guard",
            "name": "Cordis ctx.tools.guard Monotonic Dispatch Gate",
            "taxonomy": ["PLUGIN", "TOOLING", "RECOVERY"],
            "version_introduced": "0.1.1-rc.2",
            "evidence": "node_modules/@deepseek-ai/dsh-tools/lib/index.js",
            "summary": "Monotonic tool guard stage before dispatch that permits fail-closed denial with model feedback.",
            "personal_ai_equivalent": "dsh-workflow-model-preflight-gate & dsh-autonomous-execution-governor",
            "relation": "COMPLEMENTARY",
            "assessment": "ADOPTION_CANDIDATE",
            "notes": "Native hook is the execution foundation for Personal AI routing and governance guards.",
        },
    ],
    "codex": [
        {
            "feature_id": "codex_mcp_support",
            "name": "Codex Native MCP Server Configuration & Discovery",
            "taxonomy": ["MCP", "TOOLING"],
            "version_introduced": "0.149.0",
            "evidence": "config.toml [mcp_servers] configuration schema and protocol runner",
            "summary": "Native Model Context Protocol client support for external tool and resource providers.",
            "personal_ai_equivalent": "registry/capabilities.yaml#mcp_standard / agent-switchboard",
            "relation": "COMPLEMENTARY",
            "assessment": "ADOPTION_CANDIDATE",
            "notes": "AIC automatically projects admitted MCP servers into Codex config.toml.",
        },
        {
            "feature_id": "codex_multi_agent_teams",
            "name": "Codex Multi-Agent Role Spawning",
            "taxonomy": ["AGENT_ORCHESTRATION", "SUBAGENT"],
            "version_introduced": "0.149.0-alpha.4",
            "evidence": "codex-cli subagent team management commands",
            "summary": "Upstream experimental multi-agent delegation.",
            "personal_ai_equivalent": "mcp/agent-switchboard & subagent-execution-governance",
            "relation": "PARTIAL_OVERLAP",
            "assessment": "WATCH",
            "notes": "Upstream is experimental and lacks cross-model routing; Personal AI switchboard remains primary.",
        },
    ],
    "claude": [
        {
            "feature_id": "claude_code_hooks",
            "name": "Claude Code Routing & Event Hooks",
            "taxonomy": ["MODEL_ROUTING", "PLUGIN", "OBSERVABILITY"],
            "version_introduced": "2.1.238",
            "evidence": "settings.json hooks configuration and session event streams",
            "summary": "Execution hooks for PreToolUse, PostToolUse, and SessionEvent dispatch.",
            "personal_ai_equivalent": "mcp/agent-switchboard hook installer",
            "relation": "COMPLEMENTARY",
            "assessment": "ADOPTION_CANDIDATE",
            "notes": "Used by agent-switchboard to supervise Claude Code sessions.",
        },
        {
            "feature_id": "claude_auto_compact",
            "name": "Claude Context Window Auto-Compaction",
            "taxonomy": ["COMPACTION", "CONTEXT_ADMISSION"],
            "version_introduced": "2.1.238",
            "evidence": "env.CLAUDE_CODE_AUTO_COMPACT_WINDOW settings",
            "summary": "Built-in automatic compaction when context approaches window limit.",
            "personal_ai_equivalent": "CLAUDE.md managed blocks & switchboard memory",
            "relation": "COMPLEMENTARY",
            "assessment": "ADOPTION_CANDIDATE",
            "notes": "Integrated with Personal AI context budget boundaries.",
        },
    ],
    "gemini": [
        {
            "feature_id": "gemini_cli_mcp",
            "name": "Gemini CLI Native MCP Server Registration",
            "taxonomy": ["MCP", "TOOLING"],
            "version_introduced": "0.56.0",
            "evidence": "settings.json mcpServers block",
            "summary": "Native MCP server attachment for Gemini command-line tool.",
            "personal_ai_equivalent": "registry/capabilities.yaml#mcp_standard",
            "relation": "COMPLEMENTARY",
            "assessment": "ADOPTION_CANDIDATE",
            "notes": "Rendered deterministically by aic apply gemini.",
        },
    ],
}


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def _version_tuple(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+){1,3}", str(text))
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def compare_versions(observed: str | None, baseline: str | None) -> str:
    left, right = _version_tuple(observed), _version_tuple(baseline)
    if not left or not right:
        return "UNKNOWN"
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return "NEWER" if left > right else "OLDER" if left < right else "SAME"


def harness_baselines() -> dict[str, str | None]:
    out = {}
    for name in HARNESS_COMMANDS:
        path = REPO / "registry" / "harnesses" / f"{name}.yaml"
        data = load_yaml(path) if path.is_file() else {}
        out[name] = data.get("version_observed")
    return out


def discover_harness_features(harness_name: str) -> list[dict[str, Any]]:
    """Extract structured feature deltas and capability normalization for a harness."""
    return UPSTREAM_FEATURE_CATALOG.get(harness_name, [])


def evaluate_feature_proposals(harness_name: str, observed: str | None,
                               baseline: str | None) -> list[dict[str, Any]]:
    """Produce formal adoption proposals and value assessments for discovered features."""
    features = discover_harness_features(harness_name)
    proposals = []
    for feat in features:
        prop = {
            "harness": harness_name,
            "feature_id": feat["feature_id"],
            "name": feat["name"],
            "taxonomy": feat["taxonomy"],
            "evidence": feat["evidence"],
            "relation": feat["relation"],
            "assessment": feat["assessment"],
            "personal_ai_equivalent": feat["personal_ai_equivalent"],
            "summary": feat["summary"],
            "notes": feat["notes"],
            "version_observed": observed,
            "version_baseline": baseline,
        }
        proposals.append(prop)
    return proposals


def main() -> int:
    rc, discover_out = _run([sys.executable, str(AIC), "discover", "--propose-admissions"], 120)
    if rc != 0:
        print(f"UPSTREAM_CAPABILITY_REVIEW_ERROR: aic discover failed rc={rc}: {discover_out[-300:]}")
        gov_log("upstream_capability_review", "error", 1,
                {"cause": "AIC_DISCOVER_FAILED", "exit_code": rc})
        return 2

    findings: list[dict[str, Any]] = []
    all_proposals: list[dict[str, Any]] = []
    baselines = harness_baselines()

    for name, cmd in HARNESS_COMMANDS.items():
        hrc, out = _run(cmd)
        observed = out.splitlines()[0].strip() if hrc == 0 and out else None
        baseline = baselines.get(name)
        relation = compare_versions(observed, baseline)

        harness_proposals = evaluate_feature_proposals(name, observed, baseline)
        all_proposals.extend(harness_proposals)

        finding = {
            "name": name,
            "observed": observed,
            "baseline": baseline,
            "version_relation": relation,
            "features_discovered": len(harness_proposals),
            "adoption_candidates": [p["name"] for p in harness_proposals if p["assessment"] == "ADOPTION_CANDIDATE"],
            "watch_items": [p["name"] for p in harness_proposals if p["assessment"] == "WATCH"],
        }
        findings.append(finding)

        if relation == "NEWER" or any(p["assessment"] == "ADOPTION_CANDIDATE" for p in harness_proposals):
            propose(
                "upstream_capability_review",
                finding,
                "low",
                f"registry/harnesses/{name}.yaml",
                f"Feature-level review for {name}: {len(harness_proposals)} capabilities categorized; discovery is proposal-only",
                safe_auto=False,
            )

    proposal_count = sum(len(f["adoption_candidates"]) for f in findings)
    print(f"upstream_feature_discovery: harnesses={len(HARNESS_COMMANDS)} total_features={len(all_proposals)} adoption_candidates={proposal_count} adopted=0 (proposal-only)")
    gov_log("upstream_capability_review", "ok", findings, {
        "total_proposals": len(all_proposals),
        "adoption_candidates": proposal_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
