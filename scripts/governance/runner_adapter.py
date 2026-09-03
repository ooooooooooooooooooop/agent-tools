#!/usr/bin/env python3
"""PersonalAI scheduler result adapter.

The governance checks keep their existing exit-code contracts: a non-zero
check result may be a finding rather than a process failure.  This adapter is
the scheduler boundary.  It records both dimensions and returns non-zero only
when the runner could not complete its required work or persist its evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from common import SECRET_RE, gov_log  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = Path(os.environ.get("TEMP") or os.environ.get("TMP") or (Path.home() / "AppData" / "Local" / "Temp")) \
    / "personalai-governance"
EXIT_CODES = {
    "FAILED": 20,
    "TIMEOUT": 21,
    "ENVIRONMENT_ERROR": 22,
    "ENTRYPOINT_ERROR": 23,
    "EVIDENCE_WRITE_ERROR": 24,
}
VALID_SYNC_RESULTS = {"PASS": 0, "REVIEW": 1, "BLOCKED": 2}
FAILURE_MARKERS = (
    "Traceback (most recent call last):",
    "ModuleNotFoundError:",
    "ImportError:",
    "FileNotFoundError:",
    "PermissionError:",
    "SyntaxError:",
    "UPSTREAM_CAPABILITY_REVIEW_ERROR:",
    "No module named",
    "cannot find the path specified",
    "The system cannot find the path specified",
)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _redact(text: str) -> str:
    return SECRET_RE.sub("[REDACTED_SECRET]", text)


def _append_log(path: Path, text: str, evidence_errors: list[str]) -> None:
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except (OSError, UnicodeError) as exc:
        evidence_errors.append(f"log write failed: {exc}")


def _has_failure_marker(output: str) -> bool:
    return any(marker in output for marker in FAILURE_MARKERS)


def _status_from_overall(output: str) -> str | None:
    match = re.search(r"OVERALL\s*=\s*(HEALTHY|UNKNOWN|DEGRADED|BLOCKED)", output)
    if not match:
        return None
    return {"HEALTHY": "PASS", "UNKNOWN": "WARN", "DEGRADED": "DEGRADED", "BLOCKED": "BLOCKED"}[match.group(1)]


def _standard_domain_status(spec: dict[str, Any], returncode: int, output: str) -> str:
    overall = _status_from_overall(output)
    if overall:
        return overall
    if returncode == 0:
        for pattern in spec.get("review_patterns", []):
            if re.search(pattern, output, flags=re.IGNORECASE):
                return "REVIEW"
        return "PASS"
    return spec.get("nonzero_status", "DEGRADED")


def classify_standard_step(spec: dict[str, Any], returncode: int, output: str) -> dict[str, Any]:
    """Classify one existing check without changing its process contract."""
    if returncode not in spec["expected_codes"]:
        return {
            "execution_status": "FAILED",
            "failure_kind": "UNEXPECTED_EXIT_CODE",
            "domain_status": "UNKNOWN",
            "reason": f"exit={returncode}, expected={sorted(spec['expected_codes'])}",
        }
    if _has_failure_marker(output):
        return {
            "execution_status": "FAILED",
            "failure_kind": "CHILD_PROCESS_ERROR",
            "domain_status": "UNKNOWN",
            "reason": "child output contains an execution/import/error marker",
        }
    marker = spec.get("marker")
    if marker and not re.search(marker, output, flags=re.IGNORECASE):
        return {
            "execution_status": "FAILED",
            "failure_kind": "MISSING_RESULT_MARKER",
            "domain_status": "UNKNOWN",
            "reason": f"expected result marker not found: {marker}",
        }
    return {
        "execution_status": "SUCCESS",
        "failure_kind": None,
        "domain_status": _standard_domain_status(spec, returncode, output),
        "reason": "completed with a recognized check result",
    }


def classify_sync_step(returncode: int, output: str) -> dict[str, Any]:
    """Validate Sync CLI's explicit PASS/REVIEW/BLOCKED contract."""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        return {
            "execution_status": "FAILED",
            "failure_kind": "INVALID_STRUCTURED_RESULT",
            "domain_status": "UNKNOWN",
            "reason": f"--json output is not valid JSON: {exc}",
        }
    result = payload.get("result")
    expected = VALID_SYNC_RESULTS.get(result)
    if expected is None:
        return {
            "execution_status": "FAILED",
            "failure_kind": "UNKNOWN_DOMAIN_RESULT",
            "domain_status": "UNKNOWN",
            "reason": f"unknown Sync result: {result!r}",
        }
    if returncode != expected:
        return {
            "execution_status": "FAILED",
            "failure_kind": "SYNC_EXIT_CONTRACT_MISMATCH",
            "domain_status": result,
            "reason": f"Sync result {result} requires exit={expected}, got {returncode}",
        }
    return {
        "execution_status": "SUCCESS",
        "failure_kind": None,
        "domain_status": result,
        "reason": "Sync CLI contract validated; check-only result preserved",
        "sync_payload": payload,
    }


def _spec(name: str, argv: list[str], marker: str, *, expected: set[int] = {0, 1},
          subsystem: str, severity: str = "medium", classification: str = "UNKNOWN",
          nonzero_status: str = "DEGRADED", review_patterns: list[str] | None = None,
          timeout: int = 300) -> dict[str, Any]:
    return {
        "name": name,
        "argv": argv,
        "marker": marker,
        "expected_codes": expected,
        "subsystem": subsystem,
        "severity": severity,
        "classification": classification,
        "nonzero_status": nonzero_status,
        "review_patterns": review_patterns or [],
        "timeout": timeout,
    }


def frequent_specs() -> list[dict[str, Any]]:
    py = sys.executable
    aic = str(ROOT / "scripts" / "aic" / "aic.py")
    gov = str(ROOT / "scripts" / "governance")
    personal = str(ROOT / "scripts" / "personal_ai_sync.py")
    specs = [
        _spec("canonical_writer_provenance", [py, personal, "audit", "--json"],
              r'"result"\s*:\s*"(?:PASS|REVIEW)"', expected={0, 1, 3},
              subsystem="canonical-writer-provenance", classification="REAL_ACTION_REQUIRED",
              nonzero_status="REVIEW", review_patterns=[
                  r'"result"\s*:\s*"REVIEW"',
                  r"UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION",
              ], timeout=600),
        _spec("aic.validate", [py, aic, "validate"], r"\bVALID\b|\bINVALID:",
              subsystem="control-plane", classification="UNKNOWN"),
    ]
    for target in ("dsh", "codex", "claude", "gemini", "switchboard"):
        specs.append(_spec(
            f"aic.diff.{target}", [py, aic, "diff", target], r"NO DRIFT|DRIFT detected|\[dsh-runtime\] DRIFT",
            subsystem=f"harness:{target}", classification="REAL_ACTION_REQUIRED" if target == "dsh" else "UNKNOWN",
        ))
    specs.extend([
        _spec("capability_gov", [py, f"{gov}/capability_gov.py"], r"CAPABILITY_DRIFT\s*=",
              subsystem="capabilities", classification="REAL_ACTION_REQUIRED"),
        _spec("static_gov", [py, f"{gov}/static_gov.py"], r"static files scanned=",
              subsystem="static-boundary", classification="UNKNOWN"),
        _spec("routing_gov", [py, f"{gov}/routing_gov.py"], r"routing findings=",
              subsystem="routing", classification="UNKNOWN"),
        _spec("rpo_check", [py, str(ROOT / "scripts" / "durability" / "rpo_check.py")], r"RPO overall:",
              subsystem="durability", classification="REAL_ACTION_REQUIRED"),
        _spec("gov_status", [py, f"{gov}/gov_status.py"], r"OVERALL\s*=",
              expected={0, 1, 2, 3}, subsystem="governance-aggregate", classification="EXPECTED_REVIEW"),
    ])
    return specs


def weekly_specs() -> list[dict[str, Any]]:
    py = sys.executable
    gov = str(ROOT / "scripts" / "governance")
    return [
        _spec("upstream_capability_review", [py, f"{gov}/upstream_capability_review.py"],
              r"upstream_feature_discovery:", expected={0}, subsystem="upstream-capability",
              classification="EXPECTED_REVIEW",
              review_patterns=[r"adoption_candidates=[1-9]", r"total_features=[1-9]"], timeout=600),
        _spec("model_state", [py, f"{gov}/model_state.py"], r"models=.*admission_gap_proposals=",
              expected={0}, subsystem="model-state", classification="EXPECTED_REVIEW",
              review_patterns=[r"admission_gap_proposals=[1-9]"], timeout=300),
        _spec("model_health", [py, f"{gov}/model_health.py"], r"providers=.*unreachable=",
              subsystem="model-health", classification="REAL_ACTION_REQUIRED"),
        _spec("dead_config", [py, f"{gov}/dead_config.py"], r"findings=",
              subsystem="dead-config", classification="UNKNOWN"),
        _spec("memory_gov", [py, f"{gov}/memory_gov.py"], r"records=.*findings=",
              subsystem="memory", classification="UNKNOWN"),
        _spec("dup_rules", [py, f"{gov}/dup_rules.py"], r"pairs_with_high_overlap=",
              expected={0}, subsystem="duplicate-rules", classification="UNKNOWN"),
        _spec("project_state_gov", [py, f"{gov}/project_state_gov.py"], r"novel-main:",
              subsystem="project-state", classification="STALE_FINDING", nonzero_status="REVIEW"),
        _spec("durability_gov", [py, f"{gov}/durability_gov.py"], r"FULL_DR_READINESS=",
              expected={0}, subsystem="durability", classification="UNKNOWN",
              review_patterns=[r"FULL_DR_READINESS=PARTIAL", r"recent_failures=[1-9]"], timeout=300),
    ]


def sync_specs() -> list[dict[str, Any]]:
    return [{
        "name": "personal_ai_sync.check",
        "argv": [sys.executable, str(ROOT / "scripts" / "personal_ai_sync.py"), "check", "--json"],
        "expected_codes": set(VALID_SYNC_RESULTS.values()),
        "subsystem": "sync-check",
        "severity": "medium",
        "classification": "UNKNOWN",
        "timeout": 600,
    }]


def _step_result(spec: dict[str, Any], log_path: Path, evidence_errors: list[str],
                 env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    command = [str(x) for x in spec["argv"]]
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec["timeout"],
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        output = _redact((exc.stdout or "") + (exc.stderr or ""))
        if output:
            print(output)
            _append_log(log_path, output, evidence_errors)
        return {
            "name": spec["name"],
            "command": command,
            "process_exit": None,
            "execution_status": "TIMEOUT",
            "failure_kind": "TIMEOUT",
            "domain_status": "UNKNOWN",
            "finding_count": 0,
            "findings": [],
            "output_excerpt": output[-2000:],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except FileNotFoundError as exc:
        return {
            "name": spec["name"],
            "command": command,
            "process_exit": None,
            "execution_status": "ENTRYPOINT_ERROR",
            "failure_kind": "ENTRYPOINT_ERROR",
            "domain_status": "UNKNOWN",
            "finding_count": 0,
            "findings": [],
            "output_excerpt": str(exc),
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except OSError as exc:
        return {
            "name": spec["name"],
            "command": command,
            "process_exit": None,
            "execution_status": "ENVIRONMENT_ERROR",
            "failure_kind": "ENVIRONMENT_ERROR",
            "domain_status": "UNKNOWN",
            "finding_count": 0,
            "findings": [],
            "output_excerpt": str(exc),
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    output = _redact((completed.stdout or "") + (completed.stderr or "")).strip()
    if output:
        print(output)
        _append_log(log_path, output, evidence_errors)
    if spec["name"] == "personal_ai_sync.check":
        classified = classify_sync_step(completed.returncode, output)
    else:
        classified = classify_standard_step(spec, completed.returncode, output)
    status = classified["domain_status"]
    findings = []
    if classified["execution_status"] == "SUCCESS" and status != "PASS":
        findings = [{
            "id": f"{spec['name']}.result",
            "subsystem": spec["subsystem"],
            "severity": spec["severity"],
            "classification": spec["classification"],
            "actionable": spec["classification"] == "REAL_ACTION_REQUIRED",
            "informational": spec["classification"] == "EXPECTED_REVIEW",
            "blocks_next_run": False,
            "domain_status": status,
            "source_exit_code": completed.returncode,
        }]
    return {
        "name": spec["name"],
        "command": command,
        "process_exit": completed.returncode,
        "execution_status": classified["execution_status"],
        "failure_kind": classified.get("failure_kind"),
        "failure_reason": classified.get("reason"),
        "domain_status": status,
        "finding_count": len(findings),
        "findings": findings,
        "output_excerpt": output[-3000:],
        "duration_ms": round((time.monotonic() - started) * 1000),
        "sync_payload": classified.get("sync_payload"),
    }


def _aggregate_status(steps: list[dict[str, Any]]) -> str:
    statuses = [s.get("domain_status", "UNKNOWN") for s in steps]
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if any(s in ("REVIEW", "DEGRADED", "WARN") for s in statuses):
        return "REVIEW"
    return "PASS"


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_lane(lane: str) -> int:
    if lane == "frequent":
        specs = frequent_specs()
    elif lane == "weekly":
        specs = weekly_specs()
    elif lane == "sync-check":
        specs = sync_specs()
    else:
        raise ValueError(f"unknown lane: {lane}")

    started_at = now_iso()
    evidence_errors: list[str] = []
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        log_path = LOG_DIR / f"{lane}-{stamp}.log"
        receipt_path = LOG_DIR / f"{lane}-{stamp}.json"
        log_path.write_text(f"START lane={lane} root={ROOT} python={sys.executable}\n", encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"EVIDENCE_WRITE_ERROR: cannot initialize runner evidence: {exc}")
        return EXIT_CODES["EVIDENCE_WRITE_ERROR"]

    run_id = uuid.uuid4().hex
    task_ids = {
        "frequent": "PersonalAI-Governance-Frequent",
        "weekly": "PersonalAI-Governance-Weekly",
        "sync-check": "PersonalAI-Sync-Check",
    }
    child_env = os.environ.copy()
    child_env.update({
        "PERSONAL_AI_RUN_ID": run_id,
        "PERSONAL_AI_TASK_ID": os.environ.get("PERSONAL_AI_TASK_ID", task_ids[lane]),
        "PERSONAL_AI_TRIGGER": os.environ.get("PERSONAL_AI_TRIGGER", "windows-task-scheduler"),
        "PERSONAL_AI_ACTOR": os.environ.get("PERSONAL_AI_ACTOR", "personal-ai-scheduler"),
        "PERSONAL_AI_ACTOR_TYPE": os.environ.get("PERSONAL_AI_ACTOR_TYPE", "automated"),
        "PERSONAL_AI_ENTRYPOINT": str(Path(__file__).resolve()),
    })
    steps = []
    for spec in specs:
        step = _step_result(spec, log_path, evidence_errors, env=child_env)
        steps.append(step)
        _append_log(log_path, f"STEP {spec['name']} execution={step['execution_status']} "
                             f"domain={step['domain_status']} child_rc={step['process_exit']}", evidence_errors)

    execution_failures = [s for s in steps if s["execution_status"] != "SUCCESS"]
    execution_status = "SUCCESS"
    if execution_failures:
        execution_status = execution_failures[0]["execution_status"]
    if evidence_errors:
        execution_status = "EVIDENCE_WRITE_ERROR"
    governance_status = _aggregate_status(steps) if execution_status == "SUCCESS" else "UNKNOWN"
    findings = [f for s in steps for f in s.get("findings", [])]
    process_exit = 0 if execution_status == "SUCCESS" else EXIT_CODES.get(execution_status, EXIT_CODES["FAILED"])
    finished_at = now_iso()
    _append_log(log_path, f"SUMMARY execution_status={execution_status} governance_status={governance_status} "
                         f"findings={len(findings)} process_exit={process_exit}", evidence_errors)

    ledger = None
    try:
        ledger = gov_log(
            f"automation_{lane}",
            governance_status.lower(),
            len(findings),
            {
                "runner": str(Path(__file__)),
                "execution_status": execution_status,
                "governance_status": governance_status,
                "process_exit": process_exit,
                "log": str(log_path),
                "receipt": str(receipt_path),
            },
        )
        if ledger.get("_logging_limitation"):
            evidence_errors.append(ledger["_logging_limitation"])
            execution_status = "EVIDENCE_WRITE_ERROR"
            process_exit = EXIT_CODES[execution_status]
    except BaseException as exc:  # noqa: BLE001 - evidence failure must be surfaced
        evidence_errors.append(f"ledger write failed: {exc}")
        execution_status = "EVIDENCE_WRITE_ERROR"
        process_exit = EXIT_CODES[execution_status]

    receipt = {
        "schema_version": 1,
        "contract": "PERSONAL_AI_AUTOMATION_RESULT_SEMANTICS_V1",
        "lane": lane,
        "runner": str(Path(__file__)),
        "root": str(ROOT),
        "python": sys.executable,
        "run_id": run_id,
        "task_id": child_env["PERSONAL_AI_TASK_ID"],
        "actor": child_env["PERSONAL_AI_ACTOR"],
        "actor_type": child_env["PERSONAL_AI_ACTOR_TYPE"],
        "entrypoint": str(Path(__file__).resolve()),
        "pid": os.getpid(),
        "ppid": os.getppid() if hasattr(os, "getppid") else "UNKNOWN",
        "started_at": started_at,
        "finished_at": finished_at,
        "execution_status": execution_status,
        "governance_status": governance_status,
        "process_exit": process_exit,
        "findings_count": len(findings),
        "check_only": lane == "sync-check",
        "steps": steps,
        "findings": findings,
        "evidence": {"log": str(log_path), "receipt": str(receipt_path), "ledger_record": ledger},
        "evidence_errors": evidence_errors,
    }
    try:
        _write_receipt(receipt_path, receipt)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        print(f"EVIDENCE_WRITE_ERROR: cannot write receipt: {exc}")
        return EXIT_CODES["EVIDENCE_WRITE_ERROR"]

    print(f"RESULT execution_status={execution_status} governance_status={governance_status} "
          f"findings={len(findings)} process_exit={process_exit} receipt={receipt_path}")
    return process_exit


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - keep scheduler output best-effort
        pass
    parser = argparse.ArgumentParser(description="PersonalAI scheduler result adapter")
    parser.add_argument("lane", choices=["frequent", "weekly", "sync-check"])
    args = parser.parse_args()
    return run_lane(args.lane)


if __name__ == "__main__":
    sys.exit(main())
