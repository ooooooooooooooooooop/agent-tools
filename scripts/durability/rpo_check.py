#!/usr/bin/env python3
"""rpo_check.py — RPO monitoring from the run ledger (verified backups only).

HEALTHY = latest VERIFIED backup age <= RPO target. A scheduled task "succeeding"
proves nothing; only ledger rows with integrity_status=verified count.
UNKNOWN = no verified backup exists at all.

--simulate-age <dataset> <hours>  : pretend latest backup is N hours old (testing).
"""
from __future__ import annotations

import datetime as dt
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
        return {"dataset": dataset, "rpo_age_h": None, "rpo_target_h": target_h,
                "status": "UNKNOWN"}
    age = simulate_hours if simulate_hours is not None else age_hours(latest["finished_at"])
    return {"dataset": dataset, "rpo_age_h": round(age, 2), "rpo_target_h": target_h,
            "status": "HEALTHY" if age <= target_h else "BREACHED",
            "last_verified": None if latest is None else latest.get("finished_at")}


def main() -> int:
    targets = device_config()["rpo_targets_hours"]
    rows = ledger_rows()
    sim = None
    if "--simulate-age" in sys.argv:
        i = sys.argv.index("--simulate-age")
        sim = (sys.argv[i + 1], float(sys.argv[i + 2]))
    results = []
    for ds, th in targets.items():
        if ds == "repos":
            # repo RPO = remote-push health from latest check_repos rows
            latest = {}
            for r in rows:
                if r.get("job") == "check_repos":
                    latest[r["dataset"]] = r
            if not latest:
                results.append({"dataset": "repos", "status": "UNKNOWN",
                                "rpo_age_h": None, "rpo_target_h": th})
            else:
                risks = [k for k, v in latest.items() if v.get("status") != "ok"]
                results.append({"dataset": "repos", "rpo_age_h": 0, "rpo_target_h": th,
                                "status": "BREACHED" if risks else "HEALTHY",
                                "risk_repos": risks})
            continue
        results.append(evaluate(ds, rows, th,
                                sim[1] if sim and sim[0] == ds else None))
    worst = "HEALTHY"
    for r in results:
        print(f"{r['dataset']}: {r['status']} age={r['rpo_age_h']}h target={r['rpo_target_h']}h")
        if r["status"] == "BREACHED":
            worst = "BREACHED"
        elif r["status"] == "UNKNOWN" and worst == "HEALTHY":
            worst = "UNKNOWN"
    print(f"RPO overall: {worst}  (as of {now_iso()})")
    return {"HEALTHY": 0, "UNKNOWN": 2, "BREACHED": 1}[worst]


if __name__ == "__main__":
    sys.exit(main())
