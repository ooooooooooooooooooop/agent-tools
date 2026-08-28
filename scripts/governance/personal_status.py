#!/usr/bin/env python3
"""personal_status.py — 单一人类可读状态视图（Operations Mode entrypoint）。

聚合现有 checks 的输出（不重新实现底层检查）：
  Infrastructure / Personalization / Durability / Governance / Proposals / External blockers
每个运维域必须同时输出：
  status: HEALTHY / DEGRADED / BLOCKED / UNKNOWN
  evidence_state: CURRENT / LAST_KNOWN / UNAVAILABLE
  reason: 关键证据说明
  cause: 明确原因代码
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INBOX = Path.home() / ".dsh" / ".evolution-inbox" / "proposals"
PERSONALIZATION_SCRIPT = (REPO / "skills" / "personal-ai-operations-review" /
                          "scripts" / "personalization_status.py")
DEFAULT_BASELINE = (REPO / "output" / "pref-calibration" /
                    "PERSONAL_AI_PREFERENCE_CALIBRATION_REPORT.md")

KNOWN_EXTERNAL_BLOCKERS = [
    "BACKUP_KEY_CUSTODY=WAITING_FOR_CUSTODY_ROOT",
    "NOVEL_REPO_DURABILITY=BLOCKED_PRIVACY",
]


def classify_action(domain_results: dict[str, dict | tuple],
                    known_blockers: list[str]) -> str:
    """Cause-aware Overall Action 分类。

    ACTION REQUIRED:
      存在当前证据确认的新真实运行故障：
      - actual Harness drift / aic validate failure
      - actual backup age RPO breach (sessions/broker/configs 等超期)
      - scope leakage
      - secret exposure
      - governance rule check failure (非 sandbox limitation)
      - 真实未推送的普通 repo (REPO_UNPUSHED)

    REVIEW:
      - 任何域 evidence_state 为 UNAVAILABLE 或 LAST_KNOWN (证据不可用或使用旧快照)
      - Personalization DEGRADED (非致命恶化或 over-personalization)
      - Proposals REVIEW / DEGRADED (有高危待决提案)
      - 未明确分类的非关键降级

    EXTERNAL BLOCKER:
      - 仅有已知外部 blocker (BACKUP_KEY_CUSTODY, NOVEL_REPO_DURABILITY 等)，无新异常

    NO ACTION:
      - 全部 HEALTHY 且 CURRENT，无 blocker
    """
    normalized: dict[str, dict] = {}
    for k, v in domain_results.items():
        if isinstance(v, tuple):
            normalized[k] = {"status": v[0], "reason": v[1] if len(v) > 1 else "", "evidence_state": "CURRENT"}
        elif isinstance(v, dict):
            normalized[k] = v
        else:
            normalized[k] = {"status": str(v), "evidence_state": "CURRENT"}

    # 1. 检查是否有 ACTION REQUIRED 级别的硬故障
    infra = normalized.get("Infrastructure", {})
    if infra.get("status") == "DEGRADED" and infra.get("cause") in ("HARNESS_DRIFT", "VALIDATE_FAILED"):
        return "ACTION REQUIRED"

    dur = normalized.get("Durability", {})
    if dur.get("status") in ("DEGRADED", "BREACHED"):
        if dur.get("cause") in ("BACKUP_RPO_AGE_BREACH", "REPO_UNPUSHED", "CORRUPTED_BACKUP"):
            return "ACTION REQUIRED"

    pers = normalized.get("Personalization", {})
    if pers.get("status") == "ACTION REQUIRED" or pers.get("cause") == "SCOPE_LEAKAGE":
        return "ACTION REQUIRED"

    gov = normalized.get("Governance", {})
    if gov.get("status") == "DEGRADED" and gov.get("cause") == "GOVERNANCE_CHECK_FAILED":
        return "ACTION REQUIRED"

    sec = normalized.get("Secrets", {})
    if sec.get("status") == "BLOCKED":
        return "ACTION REQUIRED"

    # 2. 检查是否有 REVIEW 级别的项 (证据不可用 / LAST_KNOWN / 非关键降级 / 待审提案)
    has_unavailable = any(
        d.get("evidence_state") in ("UNAVAILABLE", "LAST_KNOWN") or d.get("status") == "UNKNOWN"
        for k, d in normalized.items() if k != "External Blockers"
    )
    if has_unavailable:
        return "REVIEW"

    if pers.get("status") == "DEGRADED":
        return "REVIEW"

    prop = normalized.get("Proposals", {})
    if prop.get("status") in ("REVIEW", "DEGRADED"):
        return "REVIEW"

    # 3. 检查是否有已知外部 blocker
    has_known_dur_blocker = dur.get("cause") == "KNOWN_PRIVACY_BLOCKER"
    if known_blockers or has_known_dur_blocker:
        return "EXTERNAL BLOCKER"

    # 4. 全部健康
    return "NO ACTION"


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def py(script: str, *args: str) -> tuple[int, str]:
    return run([sys.executable, str(REPO / "scripts" / script), *args])


def evaluate_all() -> dict[str, dict]:
    """执行全部域检查并返回结构化数据。"""
    # 1. Infrastructure
    rc, out = py("aic/aic.py", "validate")
    drifts = []
    diff_failed = False
    for t in ("dsh", "codex", "claude", "gemini", "switchboard"):
        r, _ = py("aic/aic.py", "diff", t)
        if r != 0:
            drifts.append(t)
    if rc == 0 and not drifts:
        infra_status = "HEALTHY"
        infra_state = "CURRENT"
        infra_cause = None
        infra_reason = "aic VALID, 5 targets NO DRIFT"
    elif drifts:
        infra_status = "DEGRADED"
        infra_state = "CURRENT"
        infra_cause = "HARNESS_DRIFT"
        infra_reason = f"harness drift: {drifts}"
    else:
        infra_status = "DEGRADED"
        infra_state = "CURRENT"
        infra_cause = "VALIDATE_FAILED"
        infra_reason = f"aic validate failed (rc={rc})"

    # 2. Personalization
    if PERSONALIZATION_SCRIPT.is_file():
        rc3, out3 = run([sys.executable, str(PERSONALIZATION_SCRIPT), "--json"])
        try:
            pj = json.loads(out3)
            pers_status = pj.get("status", "UNKNOWN")
            pers_state = pj.get("evidence_state", "CURRENT")
            pers_cause = pj.get("cause")
            pers_reason = pj.get("reason", "")
        except json.JSONDecodeError:
            # fallback string parsing
            first = out3.splitlines()[0] if out3 else ""
            m = re.match(r"Personalization:\s*(\w+(?:\s\w+)?)\s*(?:\[(\w+)\])?\s*—\s*(.*)", first)
            if m:
                pers_status = m.group(1)
                pers_state = m.group(2) or ("CURRENT" if rc3 == 0 else "UNKNOWN")
                pers_reason = m.group(3)
                pers_cause = None
            else:
                pers_status = "UNKNOWN"
                pers_state = "UNAVAILABLE"
                pers_cause = "PARSE_ERROR"
                pers_reason = first or "failed to parse personalization output"
    else:
        # 尝试读取 baseline 作为 LAST_KNOWN 证据
        if DEFAULT_BASELINE.is_file():
            text = DEFAULT_BASELINE.read_text(encoding="utf-8")
            cr = re.search(r"Correction Rate[^\d]*(\d+(?:\.\d+)?)%", text)
            if cr:
                pers_status = "UNKNOWN"
                pers_state = "LAST_KNOWN"
                pers_cause = "CHECK_SCRIPT_MISSING"
                pers_reason = f"current check unavailable; last verified Correction Rate {cr.group(1)}%"
            else:
                pers_status = "UNKNOWN"
                pers_state = "UNAVAILABLE"
                pers_cause = "NO_EVIDENCE"
                pers_reason = "personalization_status.py 不存在且基线无可用数据"
        else:
            pers_status = "UNKNOWN"
            pers_state = "UNAVAILABLE"
            pers_cause = "NO_EVIDENCE"
            pers_reason = "personalization_status.py 不存在"

    # 3. Durability
    sys.modules.pop("common", None)
    sys.path.insert(0, str(REPO / "scripts" / "durability"))
    try:
        import rpo_check
        rpo_res = rpo_check.check_all()
        dur_raw_status = rpo_res.get("status", "UNKNOWN")
        dur_overall_cause = rpo_res.get("overall_cause")
        backup_age_status = rpo_res.get("backup_age_status", "UNKNOWN")
        repo_res = rpo_res.get("datasets", {}).get("repos", {})
        repo_cause = repo_res.get("cause")

        if backup_age_status == "BREACHED":
            dur_status = "DEGRADED"
            dur_state = "CURRENT"
            dur_cause = "BACKUP_RPO_AGE_BREACH"
            breached_ds = [k for k, v in rpo_res.get("datasets", {}).items() if k != "repos" and v.get("status") == "BREACHED"]
            dur_reason = f"backup age exceeded target for {breached_ds} (cause: BACKUP_RPO_AGE_BREACH)"
        elif repo_cause == "KNOWN_PRIVACY_BLOCKER" and backup_age_status == "HEALTHY":
            dur_status = "DEGRADED"
            dur_state = "CURRENT"
            dur_cause = "KNOWN_PRIVACY_BLOCKER"
            dur_reason = "novel-main remains known BLOCKED_PRIVACY; backup-age datasets healthy"
        elif repo_cause == "REPO_UNPUSHED":
            dur_status = "DEGRADED"
            dur_state = "CURRENT"
            dur_cause = "REPO_UNPUSHED"
            dur_reason = f"unpushed commits in repos: {repo_res.get('risk_repos', [])}"
        elif dur_raw_status == "HEALTHY":
            dur_status = "HEALTHY"
            dur_state = "CURRENT"
            dur_cause = None
            dur_reason = "all verified backup datasets within RPO window"
        else:
            dur_status = "DEGRADED" if dur_raw_status == "BREACHED" else dur_raw_status
            dur_state = "CURRENT"
            dur_cause = dur_overall_cause
            dur_reason = f"rpo status={dur_raw_status} cause={dur_overall_cause}"
    except Exception as exc:  # noqa: BLE001
        dur_status = "UNKNOWN"
        dur_state = "UNAVAILABLE"
        dur_cause = "OBSERVABILITY_EVIDENCE_LIMITATION"
        dur_reason = f"durability check unavailable ({exc})"

    # 4. Governance
    sys.modules.pop("common", None)
    sys.path.insert(0, str(REPO / "scripts" / "governance"))
    try:
        rc_cap, out_cap = py("governance/capability_gov.py")
        rc_stat, out_stat = py("governance/static_gov.py")
        if rc_cap == 0 and rc_stat == 0:
            gov_status = "HEALTHY"
            gov_state = "CURRENT"
            gov_cause = None
            gov_reason = "capability_drift=0; static boundary clean"
        else:
            # 检查是否因沙箱限制
            if "PermissionError" in (out_cap + out_stat) or "sandbox" in (out_cap + out_stat):
                gov_status = "UNKNOWN"
                gov_state = "UNAVAILABLE"
                gov_cause = "OBSERVABILITY_EVIDENCE_LIMITATION"
                gov_reason = "current sandbox cannot read required evidence"
            else:
                gov_status = "DEGRADED"
                gov_state = "CURRENT"
                gov_cause = "GOVERNANCE_CHECK_FAILED"
                gov_reason = f"capability_rc={rc_cap} static_rc={rc_stat}"
    except Exception as exc:  # noqa: BLE001
        gov_status = "UNKNOWN"
        gov_state = "UNAVAILABLE"
        gov_cause = "OBSERVABILITY_EVIDENCE_LIMITATION"
        gov_reason = f"governance check error: {exc}"

    # 5. Proposals
    open_props, high = [], []
    prop_state = "CURRENT"
    if INBOX.is_dir():
        for f in INBOX.glob("gov-*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if j.get("status") == "open":
                open_props.append(j)
                if j.get("severity") in ("high", "critical"):
                    high.append(j.get("id"))
    prop_status = "HEALTHY" if not high else "REVIEW"
    prop_cause = "HIGH_SEVERITY_PROPOSAL" if high else None
    prop_reason = (f"{len(open_props)} open, no new high severity" if not high
                   else f"HIGH severity open: {high}")

    # 6. External Blockers
    ext_status = "BLOCKED"
    ext_state = "CURRENT"
    ext_cause = "KNOWN_EXTERNAL_BLOCKER"
    ext_reason = "known, unchanged"

    return {
        "Infrastructure": {
            "status": infra_status,
            "evidence_state": infra_state,
            "cause": infra_cause,
            "reason": infra_reason,
        },
        "Personalization": {
            "status": pers_status,
            "evidence_state": pers_state,
            "cause": pers_cause,
            "reason": pers_reason,
        },
        "Durability": {
            "status": dur_status,
            "evidence_state": dur_state,
            "cause": dur_cause,
            "reason": dur_reason,
        },
        "Governance": {
            "status": gov_status,
            "evidence_state": gov_state,
            "cause": gov_cause,
            "reason": gov_reason,
        },
        "Proposals": {
            "status": prop_status,
            "evidence_state": prop_state,
            "cause": prop_cause,
            "reason": prop_reason,
        },
        "External Blockers": {
            "status": ext_status,
            "evidence_state": ext_state,
            "cause": ext_cause,
            "reason": ext_reason,
        },
    }


def format_status_output(domains: dict[str, dict], action: str) -> str:
    lines = ["Personal AI Status", ""]
    for name, data in domains.items():
        st = data.get("status", "UNKNOWN")
        ev = data.get("evidence_state", "CURRENT")
        rs = data.get("reason", "")
        lines.append(f"{name:<18s} {st}")
        lines.append(f"  {ev} — {rs}")
        lines.append("")
    lines.append(f"Overall: {action}")
    return "\n".join(lines).strip()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="Personal AI Operations Status")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--legacy", action="store_true", help="Output legacy single-line format")
    args = parser.parse_args()

    domains = evaluate_all()
    action = classify_action(domains, KNOWN_EXTERNAL_BLOCKERS)

    if args.json:
        payload = {
            "domains": domains,
            "known_external_blockers": KNOWN_EXTERNAL_BLOCKERS,
            "action": action,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.legacy:
        for name, data in domains.items():
            st = data.get("status", "UNKNOWN")
            rs = data.get("reason", "")
            ev = data.get("evidence_state", "CURRENT")
            print(f"  [{st:8s}] {name:16s}: {ev} — {rs}")
        print(f"\nOVERALL = {action}")
    else:
        print(format_status_output(domains, action))

    return 0 if action in ("NO ACTION", "EXTERNAL BLOCKER") else 1


if __name__ == "__main__":
    sys.exit(main())
