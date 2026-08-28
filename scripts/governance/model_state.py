#!/usr/bin/env python3
"""model_state.py — six independent model states + ADMISSION_GAP investigation.

States (independent): DISCOVERED / REACHABLE / HEALTH_CHECKED / ADMITTED /
ROUTING_ENABLED / OBSERVED_IN_USE. For observed-in-use-but-not-admitted models
emits admission proposals to the inbox (never auto-admits, never routing-enables).
"""
from __future__ import annotations

import json
import sys
import tomllib
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (REPO, gov_log, load_canonical, now_iso, private_gateways,  # noqa: E402
                    propose)

HOME = Path.home()
TRACE = REPO / "registry" / "model-trace.jsonl"


def discovered() -> set[str]:
    inv = REPO / "registry" / "inventory" / "models-discovered.json"
    if inv.is_file():
        d = json.loads(inv.read_text(encoding="utf-8"))
        return {m if isinstance(m, str) else m.get("id") for m in d.get("models", [])}
    return set()


def reachable(timeout: float = 4.0) -> set[str]:
    pg = private_gateways().get("gateways", {})
    out = set()
    cpa = pg.get("cpa", {})
    if cpa.get("listen"):
        try:
            with urllib.request.urlopen(f"http://{cpa['listen']}/v1/models",
                                        timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            out = {m["id"] for m in data.get("data", [])}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):  # reachable but auth-gated
                out = {"<auth-required>"}
        except Exception:  # noqa: BLE001
            pass
    return out


def observed_in_use() -> dict[str, list[str]]:
    """model -> [who references it] from live harness configs."""
    use: dict[str, list[str]] = {}

    def add(m: str | None, who: str):
        if m:
            use.setdefault(m, []).append(who)

    pgw = private_gateways().get("gateways", {})
    try:
        cfg = tomllib.loads((HOME / ".codex" / "config.toml").read_text(encoding="utf-8-sig"))
        add(cfg.get("model"), "codex:default")
    except Exception:  # noqa: BLE001
        pass
    try:
        cl = json.loads((HOME / ".claude" / "settings.json").read_text(encoding="utf-8-sig"))
        env = cl.get("env", {})
        for k, v in env.items():
            if k.endswith("_MODEL_NAME") or k == "CLAUDE_CODE_SUBAGENT_MODEL":
                add(v, f"claude:{k}")
    except Exception:  # noqa: BLE001
        pass
    try:
        sb = json.loads((HOME / ".agent-broker" / "config.json").read_text(encoding="utf-8-sig"))
        for m in sb.get("cli_backends", {}).get("cpa", {}).get("models", []):
            add(m, "switchboard:cli_backends.cpa")
        for m in sb.get("providers", {}).get("CPA", {}).get("models", []):
            add(m, "switchboard:providers.CPA")
    except Exception:  # noqa: BLE001
        pass
    for alias, target in pgw.get("cc-switch", {}).get("alias_map", {}).items():
        add(target, f"cc-switch:alias:{alias}")
    return use


def trace_usage() -> set[str]:
    if not TRACE.is_file():
        return set()
    out = set()
    for line in TRACE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
            m = r.get("requested_model") or r.get("model")
            if m:
                out.add(m)
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    canon = load_canonical()
    admitted = {m["id"] for m in canon["models"]["models"]}
    rules = canon["policy"]["rules"]
    routing_enabled = {rules[k].get("model") for k in
                       ("main_default", "subagent_spawn", "subagent_fork", "compaction_summary")}
    routing_enabled |= {m for v in rules.get("fallbacks", {}).values() for m in v}
    disc, reach = discovered(), reachable()
    use, traced = observed_in_use(), trace_usage()
    all_models = sorted(admitted | disc | reach | set(use) | traced | {m for m in routing_enabled if m})

    print(f"{'model':38s} DISC REACH ADMIT ROUTE IN_USE")
    proposals = 0
    for m in all_models:
        states = {
            "DISCOVERED": m in disc or m in reach,
            "REACHABLE": m in reach,
            "ADMITTED": m in admitted,
            "ROUTING_ENABLED": m in routing_enabled,
            "OBSERVED_IN_USE": m in use or m in traced,
        }
        print(f"{m:38s} {str(states['DISCOVERED']):5s} {str(states['REACHABLE']):5s} "
              f"{str(states['ADMITTED']):5s} {str(states['ROUTING_ENABLED']):5s} "
              f"{str(states['OBSERVED_IN_USE'])}")
        if states["OBSERVED_IN_USE"] and not states["ADMITTED"]:
            refs = use.get(m, ["model-trace"])
            p = propose("model_admission",
                        {"model": m, "referenced_by": refs,
                         "reachable": states["REACHABLE"], "discovered": states["DISCOVERED"],
                         "question": "admit / replace_usage / keep_overlay / remove_usage"},
                        "medium", "registry/models.yaml",
                        "ADMIT_PROPOSAL or REPLACE_USAGE_PROPOSAL or KEEP_OVERLAY_TEMPORARILY "
                        "or REMOVE_USAGE_PROPOSAL — human governance decision required")
            proposals += 1
            print(f"  -> proposal {p.name}")
    gov_log("model_state", "ok" if proposals == 0 else "findings",
            {"models": len(all_models), "admission_gap_proposals": proposals})
    print(f"\nmodels={len(all_models)} admission_gap_proposals={proposals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
