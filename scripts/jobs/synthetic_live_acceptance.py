"""synthetic_live_acceptance.py — Live acceptance script for Durable Execution Coordination Layer."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "jobs"))

from jobs import (
    DurableJobRegistry,
    EvidenceLevel,
    FinalAdjudicator,
    JobState,
    LeaseDeniedError,
    MachineCheckpoint,
    MachineCheckpointManager,
    OrchestrationState,
    ValidationState,
    WorkerIdentity,
    WorkerState,
    create_worker_identity,
    durable_execution_recovery_tick,
    is_worker_alive,
)


def run_live_acceptance() -> dict:
    """Execute the full end-to-end live synthetic acceptance drill."""
    results = {}
    with tempfile.TemporaryDirectory() as td_name:
        td = Path(td_name)
        db_path = td / "live_durable_jobs.db"
        ckpt_dir = td / "checkpoints"
        work_dir = td / "workspace"
        work_dir.mkdir(parents=True, exist_ok=True)

        registry = DurableJobRegistry(db_path)
        ckpt_mgr = MachineCheckpointManager(ckpt_dir)
        adjudicator = FinalAdjudicator()

        job_id = "job_synth_scanner_live"

        # Step 1: Create Job
        job = registry.create_job(job_id, "synthetic_scanner", str(work_dir), created_by="live_test")
        assert job.job_state == JobState.PENDING.value
        results["1_job_created"] = True

        # Step 2: Acquire Lease and Start Attempt 1
        ident1 = create_worker_identity(pid=os.getpid(), worker_type="synthetic_scanner_worker")
        att1 = registry.start_attempt(job_id, writer_id="worker_live_1", worker_type="synthetic_scanner", worker_identity=ident1.to_dict())
        assert att1.attempt_id == f"{job_id}_att_1"
        results["2_attempt_1_started"] = True

        # Step 3: Write Machine Checkpoint at progress item 50
        fixture_file = work_dir / "scanned_data.txt"
        content1 = b"item_001_to_item_050\n"
        fixture_file.write_bytes(content1)
        h1 = hashlib.sha256(content1).hexdigest()

        ckpt1 = MachineCheckpoint(
            checkpoint_version=1,
            job_id=job_id,
            attempt_id=att1.attempt_id,
            input_identity="source_tree",
            source_hashes={str(fixture_file): h1},
            cursor=50,
            partition=1,
            authorized_root=str(work_dir),
            next_operation="scan_items_51_to_100",
            created_at=str(time.time()),
        )
        cpath1 = ckpt_mgr.write_checkpoint(ckpt1)
        registry.record_checkpoint(job_id, att1.attempt_id, str(cpath1))
        results["3_checkpoint_written"] = True

        # Step 4: Orchestration session ends (round limit reached)
        registry.update_orchestration_state(job_id, OrchestrationState.ENDED_ROUND_LIMIT.value)
        job_after_round = registry.get_job(job_id)
        assert job_after_round.orchestration_state == OrchestrationState.ENDED_ROUND_LIMIT.value
        assert job_after_round.job_state == JobState.CHECKPOINTED.value
        results["4_orchestration_round_limit_isolated"] = True

        # Step 5: Worker is still alive in OS -> Recovery tick must NO_ACTION
        tick_actions_alive = durable_execution_recovery_tick(db_path)
        assert len(tick_actions_alive) == 1
        assert tick_actions_alive[0].action_type == "NO_ACTION"
        results["5_alive_worker_no_duplicate_worker"] = True

        # Step 6: Second Agent attempts to start job while worker alive -> LEASE_DENIED
        second_writer_denied = False
        try:
            registry.start_attempt(job_id, "rogue_second_writer", "scanner", {"pid": 99999, "host": "local"})
        except LeaseDeniedError:
            second_writer_denied = True
        assert second_writer_denied is True
        results["6_second_writer_strictly_denied"] = True

        # Step 7: Worker 1 terminates (simulate crash/kill)
        registry.update_worker_state(att1.attempt_id, WorkerState.EXITED_ERROR.value, exit_code=1)
        results["7_worker_1_terminated"] = True

        # Step 8: Recovery tick runs with worker dead -> resumes attempt 2 from checkpoint!
        # Mock worker liveness check to report False (dead)
        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=False):
            tick_actions_dead = durable_execution_recovery_tick(db_path)
            assert len(tick_actions_dead) == 1
            assert tick_actions_dead[0].action_type == "RESUME_ATTEMPT"
            results["8_recovery_tick_resumed_attempt_2"] = True

        job_resumed = registry.get_job(job_id)
        assert job_resumed.current_attempt_id == f"{job_id}_att_2"
        assert job_resumed.job_id == job_id  # Stable Job ID!

        # Step 9: Worker 2 finishes remaining work (items 51 to 100) and produces final artifact
        final_file = work_dir / "final_report.json"
        report_bytes = json.dumps({"status": "SUCCESS", "items_scanned": 100}, indent=2).encode("utf-8")
        final_file.write_bytes(report_bytes)
        final_hash = hashlib.sha256(report_bytes).hexdigest()

        envelope_data = {
            "task_id": job_id,
            "status": "PASS",
            "summary": "Completed scanning all 100 items",
            "validations": [{
                "name": "final_report_sha256",
                "evidence_level": "L3_REPRODUCED",
                "evidence": {
                    "file_path": str(final_file),
                    "expected_hash": final_hash,
                },
            }],
        }
        results["9_worker_2_produced_output"] = True

        # Step 10: Final Adjudicator independently recalculates hash (L3) and grants COMPLETED
        val_record = registry.final_adjudicate(
            job_id=job_id,
            attempt_id=job_resumed.current_attempt_id,
            validator_id="system_l3_validator",
            envelope_data=envelope_data,
            required_validations=[{
                "name": "final_report_sha256",
                "required_evidence_level": "L3_REPRODUCED",
            }],
        )
        assert val_record.result == ValidationState.PASS.value
        results["10_final_adjudication_passed"] = True

        final_job = registry.get_job(job_id)
        assert final_job.job_state == JobState.COMPLETED.value
        assert final_job.validation_state == ValidationState.PASS.value
        assert registry.lease_mgr.get_lease(job_id) is None  # Lease released gracefully
        results["11_canonical_job_completed_lease_released"] = True

    return results


if __name__ == "__main__":
    out = run_live_acceptance()
    print("LIVE_ACCEPTANCE=" + json.dumps(out, indent=2))
    assert all(out.values())
    print("LIVE_ACCEPTANCE_OVERALL=PASS")
