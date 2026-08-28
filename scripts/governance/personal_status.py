#!/usr/bin/env python3
"""personal_status.py — 单一人类可读状态视图（Operations Mode entrypoint）。

聚合现有 checks 的输出（不重新实现底层检查）：
  Infrastructure / Durability / Governance / Pending proposals / External blockers
每项 HEALTHY/DEGRADED/BLOCKED/UNKNOWN + 一句原因。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INBOX = Path.home() / ".dsh" / ".evolution-inbox" / "proposals"


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def py(script: str, *args: str) -> tuple[int, str]:
    return run([sys.executable, str(REPO / "scripts" / script), *args])


def main() -> int:
    # Infrastructure: control plane valid + all harness diffs clean
    rc, _ = py("aic/aic.py", "validate")
    drifts = []
    for t in ("dsh", "codex", "claude", "gemini", "switchboard"):
        r, _ = py("aic/aic.py", "diff", t)
        if r != 0:
            drifts.append(t)
    infra = "HEALTHY" if rc == 0 and not drifts else "DEGRADED"
    infra_reason = "aic VALID; 5 targets NO DRIFT" if infra == "HEALTHY" else \
        f"validate_rc={rc} drift={drifts}"

    # Durability: RPO check (verified backups only)
    rc, out = py("durability/rpo_check.py")
    dur = {0: "HEALTHY", 1: "DEGRADED"}.get(rc, "UNKNOWN")
    dur_reason = out.splitlines()[-1] if out else ""

    # Governance: last gov run freshness + module findings
    rc, out = py("governance/capability_gov.py")
    rc2, out2 = py("governance/static_gov.py")
    gov = "HEALTHY" if rc == 0 and rc2 == 0 else "DEGRADED"
    gov_reason = "capability_drift=0; static boundary clean" if gov == "HEALTHY" else \
        f"capability_rc={rc} static_rc={rc2}"

    # Pending proposals
    open_props, high = [], []
    if INBOX.is_dir():
        for f in INBOX.glob("gov-*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if j.get("status") == "open":
                open_props.append(j)
                if j.get("severity") in ("high", "critical"):
                    high.append(j["id"])
    prop = "HEALTHY" if not high else "DEGRADED"
    prop_reason = (f"{len(open_props)} open (none high-severity)" if not high
                   else f"HIGH severity open: {high}")

    print("Personal AI Status")
    print(f"  [{infra:8s}] Infrastructure : {infra_reason}")
    print(f"  [{dur:8s}] Durability    : {dur_reason}")
    print(f"  [{gov:8s}] Governance    : {gov_reason}")
    print(f"  [{prop:8s}] Proposals     : {prop_reason}")
    print("  [BLOCKED ] External      : BACKUP_KEY_CUSTODY=WAITING_FOR_CUSTODY_ROOT; "
          "NOVEL_REPO_DURABILITY=BLOCKED_PRIVACY")
    order = {"HEALTHY": 0, "UNKNOWN": 1, "DEGRADED": 2, "BLOCKED": 3}
    worst = max([infra, dur, gov, prop, "BLOCKED"], key=lambda s: order[s])
    print(f"\nOVERALL = {worst}")
    return 0 if worst == "HEALTHY" else 1


if __name__ == "__main__":
    sys.exit(main())
