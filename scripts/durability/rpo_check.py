#!/usr/bin/env python3
"""rpo_check.py — RPO monitoring from the run ledger (verified backups only).

HEALTHY = latest VERIFIED backup age <= RPO target. A scheduled task "succeeding"
proves nothing; only ledger rows with integrity_status=verified count.
UNKNOWN = no verified backup exists at all.

--simulate-age <dataset> <hours>  : pretend latest backup is N hours old (testing).
--json                            : output full structured result as JSON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import device_config, ledger_rows, now_iso  # noqa: E402


def latest_verified(rows: list[dict], dataset: str):
    best = None
    for r in rows:
        ds = r.get("dataset", "")
        if not (ds == dataset or ds.startswith(dataset + ":")):
            continue
        if r.get("status") == "ok" and r.get("integrity_status") == "verified":
            if best is None or r.get("finished_at", "") > best.get("finished_at", ""):
                best = r
    return best


def age_hours(iso: str) -> float:
    t = dt.datetime.fromisoformat(iso)
    return (dt.datetime.now().astimezone() - t).total_seconds() / 3600


def evaluate(dataset: str, rows: list[dict], target_h: float,
             simulate_hours: float | None = None) -> dict:
    latest = latest_verified(rows, dataset)
    if latest is None and simulate_hours is None:
        return {
            "dataset": dataset,
            "rpo_age_h": None,
            "rpo_target_h": target_h,
            "status": "UNKNOWN",
            "cause": "NO_VERIFIED_BACKUP",
            "last_verified": None,
        }
    age = simulate_hours if simulate_hours is not None else age_hours(latest["finished_at"])
    status = "HEALTHY" if age <= target_h else "BREACHED"
    cause = "BACKUP_RPO_AGE_BREACH" if status == "BREACHED" else None
    return {
        "dataset": dataset,
        "rpo_age_h": round(age, 2),
        "rpo_target_h": target_h,
        "status": status,
        "cause": cause,
        "last_verified": None if latest is None else latest.get("finished_at"),
    }


def evaluate_repos(rows: list[dict], target_h: float) -> dict:
    latest = {}
    for r in rows:
        if r.get("job") == "check_repos":
            latest[r["dataset"]] = r
    if not latest:
        return {
            "dataset": "repos",
            "status": "UNKNOWN",
            "rpo_age_h": None,
            "rpo_target_h": target_h,
            "cause": "NO_REPO_CHECKS",
            "risk_repos": [],
            "details": {},
        }

    risks = [k for k, v in latest.items() if v.get("status") != "ok"]
    if not risks:
        return {
            "dataset": "repos",
            "status": "HEALTHY",
            "rpo_age_h": 0,
            "rpo_target_h": target_h,
            "cause": None,
            "risk_repos": [],
            "details": latest,
        }

    privacy_risks = []
    unpushed_risks = []
    remote_risks = []
    other_risks = []

    for k in risks:
        v = latest[k]
        st = str(v.get("status", ""))
        note = str(v.get("note", ""))
        if "novel-main" in k or "BLOCKED_PRIVACY" in st or "privacy" in note.lower() or "privacy" in st.lower():
            privacy_risks.append(k)
        elif v.get("unpushed_commits", 0) > 0 or "UNPUSHED" in st:
            unpushed_risks.append(k)
        elif "REMOTE" in st or "network" in note.lower():
            remote_risks.append(k)
        else:
            other_risks.append(k)

    if unpushed_risks:
        cause = "REPO_UNPUSHED"
    elif remote_risks:
        cause = "REMOTE_UNAVAILABLE"
    elif privacy_risks:
        cause = "KNOWN_PRIVACY_BLOCKER"
    else:
        cause = "REPO_RISK"

    return {
        "dataset": "repos",
        "status": "BREACHED",
        "rpo_age_h": 0,
        "rpo_target_h": target_h,
        "cause": cause,
        "risk_repos": risks,
        "details": latest,
    }


def check_all(rows: list[dict] | None = None, targets: dict | None = None,
              simulate_hours: dict[str, float] | None = None) -> dict:
    if targets is None:
        targets = device_config()["rpo_targets_hours"]
    if rows is None:
        rows = ledger_rows()

    datasets = {}
    for ds, th in targets.items():
        if ds == "repos":
            datasets[ds] = evaluate_repos(rows, th)
        else:
            sim_h = simulate_hours.get(ds) if simulate_hours else None
            datasets[ds] = evaluate(ds, rows, th, sim_h)

    backup_age_breaches = [ds for ds, r in datasets.items() if ds != "repos" and r["status"] == "BREACHED"]
    backup_age_status = "BREACHED" if backup_age_breaches else (
        "UNKNOWN" if any(r["status"] == "UNKNOWN" for ds, r in datasets.items() if ds != "repos") else "HEALTHY"
    )

    worst = "HEALTHY"
    overall_cause = None
    for r in datasets.values():
        if r["status"] == "BREACHED":
            worst = "BREACHED"
            if r["cause"] == "BACKUP_RPO_AGE_BREACH":
                overall_cause = "BACKUP_RPO_AGE_BREACH"
            elif overall_cause is None:
                overall_cause = r["cause"]
        elif r["status"] == "UNKNOWN" and worst == "HEALTHY":
            worst = "UNKNOWN"
            if overall_cause is None:
                overall_cause = r["cause"]

    return {
        "status": worst,
        "overall_cause": overall_cause,
        "backup_age_status": backup_age_status,
        "datasets": datasets,
        "timestamp": now_iso(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check RPO status across datasets.")
    parser.add_argument("--simulate-age", nargs=2, metavar=("DATASET", "HOURS"),
                        help="Simulate backup age in hours for a dataset")
    parser.add_argument("--json", action="store_true", help="Output full JSON result")
    args = parser.parse_args()

    sim_map = {}
    if args.simulate_age:
        sim_map[args.simulate_age[0]] = float(args.simulate_age[1])

    result = check_all(simulate_hours=sim_map)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for ds, r in result["datasets"].items():
            cause_str = f" ({r['cause']})" if r.get("cause") else ""
            print(f"{ds}: {r['status']}{cause_str} age={r['rpo_age_h']}h target={r['rpo_target_h']}h")
        print(f"RPO overall: {result['status']} (cause: {result['overall_cause']})  (as of {result['timestamp']})")

    return {"HEALTHY": 0, "UNKNOWN": 2, "BREACHED": 1}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
