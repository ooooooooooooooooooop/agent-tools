#!/usr/bin/env python3
"""gov_status.py — unified governance status: "系统现在健康吗？"

Aggregates per-domain status with reasons. No unexplained aggregate score.
Domains: Control Plane / Models / Routing / Capabilities / Memory /
Project State / Harnesses / Durability / Secrets / Proposals / External blockers.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import INBOX, REPO, STATE, gov_log, load_yaml, now_iso  # noqa: E402


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           cwd=str(cwd) if cwd else None, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def aic(*args: str) -> tuple[int, str]:
    return run([sys.executable, str(REPO / "scripts" / "aic" / "aic.py"), *args])


def main() -> int:
    domains: list[dict] = []

    rc, out = aic("validate")
    domains.append({"domain": "Control Plane", "status": "HEALTHY" if rc == 0 else "DEGRADED",
                    "reason": out.splitlines()[-1] if out else "no output"})
    drift = []
    for t in ("dsh", "codex", "claude", "gemini", "switchboard"):
        rc, _ = aic("diff", t)
        if rc != 0:
            drift.append(t)
    domains.append({"domain": "Harnesses", "status": "HEALTHY" if not drift else "DEGRADED",
                    "reason": f"drift targets: {drift}" if drift else "all 5 targets NO DRIFT"})

    rc, out = run([sys.executable, str(REPO / "scripts" / "governance" / "routing_gov.py")])
    domains.append({"domain": "Routing", "status": "HEALTHY" if rc == 0 else "DEGRADED",
                    "reason": out.splitlines()[-1] if out else ""})

    rc, out = run([sys.executable, str(REPO / "scripts" / "governance" / "capability_gov.py")])
    domains.append({"domain": "Capabilities", "status": "HEALTHY" if rc == 0 else "DEGRADED",
                    "reason": out.splitlines()[-1] if out else ""})

    rc, out = run([sys.executable, str(REPO / "scripts" / "governance" / "memory_gov.py")])
    domains.append({"domain": "Memory", "status": "HEALTHY" if rc == 0 else "DEGRADED",
                    "reason": out.splitlines()[-2] if out else ""})

    rc, out = run([sys.executable, str(REPO / "scripts" / "governance" / "project_state_gov.py")])
    domains.append({"domain": "Project State", "status": "HEALTHY" if rc == 0 else "DEGRADED",
                    "reason": out.splitlines()[-1] if out else ""})

    rc, out = run([sys.executable, str(REPO / "scripts" / "durability" / "rpo_check.py")])
    dur_status = {0: "HEALTHY", 1: "DEGRADED"}.get(rc, "UNKNOWN")
    domains.append({"domain": "Durability", "status": dur_status,
                    "reason": (out.splitlines()[-1] if out else "") +
                              " | FULL_DR_READINESS=PARTIAL (BACKUP_KEY_CUSTODY)"})

    # secrets exposure (fast regex over registries only — deep scans are scheduled)
    import re
    sec = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})")
    exposed = []
    for f in (REPO / "registry").rglob("*.yaml"):
        if "inventory" in f.parts:
            continue
        if sec.search(f.read_text(encoding="utf-8-sig", errors="replace")):
            exposed.append(f.name)
    domains.append({"domain": "Secrets exposure",
                    "status": "HEALTHY" if not exposed else "BLOCKED",
                    "reason": f"exposed files: {exposed}" if exposed else "no secret-shaped strings in canonical"})

    proposals = list(INBOX.glob("gov-*.json")) if INBOX.is_dir() else []
    open_props = 0
    for p in proposals:
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("status") == "open":
                open_props += 1
        except json.JSONDecodeError:
            continue
    domains.append({"domain": "Outstanding proposals",
                    "status": "HEALTHY" if open_props == 0 else "DEGRADED",
                    "reason": f"{open_props} open proposal(s) in .evolution-inbox"})

    domains.append({"domain": "Models", "status": "DEGRADED",
                    "reason": "ADMISSION_GAP open (observed-in-use models pending admission decision)"})
    domains.append({"domain": "External blockers", "status": "BLOCKED",
                    "reason": "BACKUP_KEY_CUSTODY = WAITING_FOR_CUSTODY_ROOT; "
                              "NOVEL_REPO_DURABILITY = BLOCKED_PRIVACY (3 unpushed commits contain "
                              "pre-sanitization private infra names in history)"})

    order = {"HEALTHY": 0, "UNKNOWN": 1, "DEGRADED": 2, "BLOCKED": 3}
    worst = max((d["status"] for d in domains), key=lambda s: order[s])
    print(f"GOVERNANCE STATUS — {now_iso()}")
    for d in domains:
        print(f"  [{d['status']:8s}] {d['domain']}: {d['reason']}")
    print(f"\nOVERALL = {worst}")
    print("reasons: " + "; ".join(f"{d['domain']}={d['status']}" for d in domains
                                  if d["status"] != "HEALTHY"))
    gov_log("gov_status", worst.lower(), len(domains),
            {"overall": worst, "degraded": [d["domain"] for d in domains
                                            if d["status"] != "HEALTHY"]})
    return {"HEALTHY": 0, "UNKNOWN": 2, "DEGRADED": 1, "BLOCKED": 3}[worst]


if __name__ == "__main__":
    sys.exit(main())
