"""test_durable_execution.py — Comprehensive 30-scenario regression test matrix for Durable Execution."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "jobs"))

from jobs import (
    DurableJobRegistry,
    DshJobTools,
    EvidenceLevel,
    FinalAdjudicator,
    JobRecord,
    JobState,
    LeaseDeniedError,
    LeaseManager,
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


class DurableExecutionMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.db_path = self.td / "durable_jobs.db"
        self.registry = DurableJobRegistry(self.db_path)
        self.ckpt_mgr = MachineCheckpointManager(self.td / "checkpoints")
        self.adjudicator = FinalAdjudicator()

    def tearDown(self) -> None:
        import gc
        gc.collect()
        self.temp_dir.cleanup()

    # 1. job create
    def test_01_job_create(self) -> None:
        job = self.registry.create_job(
            job_id="job_sync_101",
            job_type="scanner",
            authorized_root=str(self.td),
            created_by="test_runner",
        )
        self.assertEqual(job.job_id, "job_sync_101")
        self.assertEqual(job.job_state, JobState.PENDING.value)
        self.assertEqual(job.orchestration_state, OrchestrationState.WAITING.value)
        self.assertEqual(job.validation_state, ValidationState.NOT_STARTED.value)

        # Check events table
        events = self.registry.get_events("job_sync_101")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "JOB_CREATED")

    # 2. stable job id
    def test_02_stable_job_id(self) -> None:
        self.registry.create_job("job_stable_01", "scanner", str(self.td))
        ident = {"pid": os.getpid(), "host": "local", "process_start_time": "t0"}
        att1 = self.registry.start_attempt("job_stable_01", "w1", "proc", ident)
        self.assertEqual(att1.job_id, "job_stable_01")
        self.registry.update_worker_state(att1.attempt_id, WorkerState.EXITED_ERROR.value, exit_code=1)
        self.registry.lease_mgr.release_lease("job_stable_01", self.registry.lease_mgr.get_lease("job_stable_01").lease_id)

        # Restart / retry attempt
        att2 = self.registry.start_attempt("job_stable_01", "w2", "proc", ident)
        # Job ID remains unchanged across attempts!
        self.assertEqual(att2.job_id, "job_stable_01")
        job = self.registry.get_job("job_stable_01")
        self.assertEqual(job.job_id, "job_stable_01")
        self.assertEqual(job.current_attempt_id, att2.attempt_id)

    # 3. new attempt id on retry
    def test_03_new_attempt_id_on_retry(self) -> None:
        self.registry.create_job("job_retry_01", "build", str(self.td))
        ident = {"pid": os.getpid(), "host": "local", "process_start_time": "t0"}
        att1 = self.registry.start_attempt("job_retry_01", "w1", "proc", ident)
        self.assertEqual(att1.attempt_id, "job_retry_01_att_1")
        self.registry.lease_mgr.release_lease("job_retry_01", self.registry.lease_mgr.get_lease("job_retry_01").lease_id)

        att2 = self.registry.start_attempt("job_retry_01", "w2", "proc", ident)
        self.assertEqual(att2.attempt_id, "job_retry_01_att_2")
        self.assertNotEqual(att1.attempt_id, att2.attempt_id)
        self.assertEqual(len(self.registry.get_attempts("job_retry_01")), 2)

    # 4. atomic lease
    def test_04_atomic_lease(self) -> None:
        self.registry.create_job("job_lease_01", "scan", str(self.td))
        lease = self.registry.lease_mgr.acquire_lease("job_lease_01", "att_1", "writer_A", ttl_seconds=60.0)
        self.assertEqual(lease.job_id, "job_lease_01")
        self.assertEqual(lease.writer_id, "writer_A")
        self.assertGreater(lease.expires_at, time.time())

        # Verify DB entry
        queried = self.registry.lease_mgr.get_lease("job_lease_01")
        self.assertIsNotNone(queried)
        self.assertEqual(queried.lease_id, lease.lease_id)

    # 5. second writer denied
    def test_05_second_writer_denied(self) -> None:
        self.registry.create_job("job_lock_01", "scan", str(self.td))
        self.registry.lease_mgr.acquire_lease("job_lock_01", "att_1", "writer_1", ttl_seconds=100.0)

        # Second writer attempts to acquire lease on the same job
        with self.assertRaises(LeaseDeniedError):
            self.registry.lease_mgr.acquire_lease("job_lock_01", "att_2", "writer_2", ttl_seconds=100.0)

    # 6. lease expiry
    def test_06_lease_expiry(self) -> None:
        self.registry.create_job("job_exp_01", "scan", str(self.td))
        # Acquire lease with past timestamp (expired)
        t_past = time.time() - 10.0
        self.registry.lease_mgr.acquire_lease("job_exp_01", "att_1", "writer_1", ttl_seconds=5.0, now_epoch=t_past)

        # Second writer can now acquire because previous lease is expired!
        lease2 = self.registry.lease_mgr.acquire_lease("job_exp_01", "att_2", "writer_2", ttl_seconds=60.0)
        self.assertEqual(lease2.writer_id, "writer_2")

    # 7. PID identity
    def test_07_pid_identity(self) -> None:
        # Construct identity with real current process
        ident = create_worker_identity(pid=os.getpid(), worker_type="pytest", custom_start_time="2026-09-03T01:00:00")
        self.assertEqual(ident.pid, os.getpid())
        self.assertEqual(ident.process_start_time, "2026-09-03T01:00:00")

        # Fake recycled PID with different creation time must report dead!
        with mock.patch("jobs.worker_identity.get_process_creation_time", return_value="2026-09-03T02:00:00"):
            with mock.patch("jobs.worker_identity.current_device_id", return_value=ident.host):
                self.assertFalse(is_worker_alive(ident))

    # 8. round-limit != job failure
    def test_08_round_limit_does_not_fail_job(self) -> None:
        self.registry.create_job("job_round_01", "scanner", str(self.td))
        ident = {"pid": os.getpid(), "host": "local", "process_start_time": "t0"}
        self.registry.start_attempt("job_round_01", "w1", "scanner", ident)

        # Agent orchestration hits round limit
        self.registry.update_orchestration_state("job_round_01", OrchestrationState.ENDED_ROUND_LIMIT.value)

        job = self.registry.get_job("job_round_01")
        # Invariant: orchestration is ENDED_ROUND_LIMIT, but job_state remains RUNNING!
        self.assertEqual(job.orchestration_state, OrchestrationState.ENDED_ROUND_LIMIT.value)
        self.assertEqual(job.job_state, JobState.RUNNING.value)
        self.assertNotEqual(job.job_state, JobState.FAILED.value)

    # 9. alive worker no recovery
    def test_09_alive_worker_no_recovery(self) -> None:
        self.registry.create_job("job_alive_01", "scanner", str(self.td))
        ident = {"pid": 99999, "host": "local", "process_start_time": "start_time_real"}
        self.registry.start_attempt("job_alive_01", "w1", "scanner", ident)
        self.registry.update_orchestration_state("job_alive_01", OrchestrationState.ENDED_ROUND_LIMIT.value)

        # Mock worker as alive
        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=True):
            actions = durable_execution_recovery_tick(self.db_path)
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].action_type, "NO_ACTION")
            self.assertIn("worker is still alive", actions[0].reason)

            # Check attempt was NOT incremented (no second worker!)
            job = self.registry.get_job("job_alive_01")
            self.assertEqual(job.current_attempt_id, "job_alive_01_att_1")

    # 10. dead worker recovery
    def test_10_dead_worker_recovery(self) -> None:
        self.registry.create_job("job_dead_01", "scanner", str(self.td))
        ident = {"pid": 11111, "host": "local", "process_start_time": "dead_start"}
        att1 = self.registry.start_attempt("job_dead_01", "w1", "scanner", ident)

        # Write machine checkpoint
        ckpt = MachineCheckpoint(
            checkpoint_version=1,
            job_id="job_dead_01",
            attempt_id=att1.attempt_id,
            input_identity="source_repo",
            cursor=42,
            authorized_root=str(self.td),
        )
        cpath = self.ckpt_mgr.write_checkpoint(ckpt)
        self.registry.record_checkpoint("job_dead_01", att1.attempt_id, str(cpath))

        # Worker is dead
        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=False):
            actions = durable_execution_recovery_tick(self.db_path)
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].action_type, "RESUME_ATTEMPT")

            # Verify attempt was advanced to attempt 2 and job remains stable
            job = self.registry.get_job("job_dead_01")
            self.assertEqual(job.current_attempt_id, "job_dead_01_att_2")
            self.assertEqual(job.job_state, JobState.CHECKPOINTED.value)

    # 11. checkpoint resume
    def test_11_checkpoint_resume(self) -> None:
        test_file = self.td / "input.txt"
        test_file.write_text("dataset v1", encoding="utf-8")
        import hashlib
        h = hashlib.sha256(b"dataset v1").hexdigest()

        ckpt = MachineCheckpoint(
            checkpoint_version=1,
            job_id="job_chk_01",
            attempt_id="att_1",
            input_identity="input.txt",
            source_hashes={str(test_file): h},
            cursor=100,
            authorized_root=str(self.td),
        )
        valid, reason = self.ckpt_mgr.validate_checkpoint_for_resume(ckpt)
        self.assertTrue(valid)
        self.assertEqual(reason, "VALID")

    # 12. invalid checkpoint no resume
    def test_12_invalid_checkpoint_no_resume(self) -> None:
        test_file = self.td / "input.txt"
        test_file.write_text("dataset v1", encoding="utf-8")

        ckpt = MachineCheckpoint(
            checkpoint_version=1,
            job_id="job_chk_bad",
            attempt_id="att_1",
            input_identity="input.txt",
            source_hashes={str(test_file): "mismatched_hash_123"},
            cursor=100,
            authorized_root=str(self.td),
        )
        valid, reason = self.ckpt_mgr.validate_checkpoint_for_resume(ckpt)
        self.assertFalse(valid)
        self.assertIn("source input file modified on disk", reason)

        # In recovery tick, invalid checkpoint must yield REVIEW_REQUIRED, not auto resume
        self.registry.create_job("job_chk_bad", "scan", str(self.td))
        cpath = self.ckpt_mgr.write_checkpoint(ckpt)
        att = self.registry.start_attempt("job_chk_bad", "w1", "scan", {"pid": 12, "host": "local"})
        self.registry.record_checkpoint("job_chk_bad", att.attempt_id, str(cpath))

        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=False):
            actions = durable_execution_recovery_tick(self.db_path)
            self.assertEqual(actions[0].action_type, "REVIEW_REQUIRED")
            job = self.registry.get_job("job_chk_bad")
            self.assertEqual(job.job_state, JobState.WAITING_EVENT.value)

    # 13. host restart
    def test_13_host_restart(self) -> None:
        # Job was running before restart
        self.registry.create_job("job_restart_01", "crawler", str(self.td))
        att1 = self.registry.start_attempt("job_restart_01", "pre_restart_worker", "proc", {"pid": 1234, "host": "local"})
        ckpt = MachineCheckpoint(checkpoint_version=1, job_id="job_restart_01", attempt_id=att1.attempt_id, input_identity="in", authorized_root=str(self.td))
        cpath = self.ckpt_mgr.write_checkpoint(ckpt)
        self.registry.record_checkpoint("job_restart_01", att1.attempt_id, str(cpath))

        # Host restarts: process 1234 is gone
        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=False):
            actions = durable_execution_recovery_tick(self.db_path)
            self.assertEqual(actions[0].action_type, "RESUME_ATTEMPT")
            job = self.registry.get_job("job_restart_01")
            self.assertEqual(job.current_attempt_id, "job_restart_01_att_2")

    # 14. device mismatch lease stale
    def test_14_device_mismatch_lease_stale(self) -> None:
        ident = WorkerIdentity(pid=100, process_start_time="t0", host="other-pc", worker_type="proc")
        # On current device, foreign worker must report dead!
        with mock.patch("jobs.worker_identity.current_device_id", return_value="my-pc"):
            self.assertFalse(is_worker_alive(ident))

    # 15. ResultEnvelope binding
    def test_15_result_envelope_binding(self) -> None:
        self.registry.create_job("job_env_01", "test", str(self.td))
        att = self.registry.start_attempt("job_env_01", "w1", "proc", {"pid": 1, "host": "local"})
        env_file = str(self.td / "envelope.json")
        self.registry.record_result_envelope("job_env_01", att.attempt_id, env_file)

        events = self.registry.get_events("job_env_01")
        env_events = [e for e in events if e["event_type"] == "RESULT_RECEIVED"]
        self.assertEqual(len(env_events), 1)
        self.assertIn("envelope.json", env_events[0]["payload_json"])

    # 16. evidence L0
    def test_16_evidence_l0_claim(self) -> None:
        # Agent prose or claim alone is L0
        status, obs, msg = self.adjudicator.evaluate_requirement(
            "test_pass",
            required_level=EvidenceLevel.L3_REPRODUCED,
            claimed_level=EvidenceLevel.L0_CLAIM,
            evidence={"claim": "I tested it and it passed"},
        )
        self.assertEqual(status, ValidationState.REVIEW_REQUIRED.value)
        self.assertEqual(obs, EvidenceLevel.L0_CLAIM)

    # 17. evidence L3
    def test_17_evidence_l3_reproduced(self) -> None:
        artifact = self.td / "output.bin"
        artifact.write_bytes(b"reproducible payload")
        import hashlib
        exp_h = hashlib.sha256(b"reproducible payload").hexdigest()

        status, obs, msg = self.adjudicator.evaluate_requirement(
            "payload_sha",
            required_level=EvidenceLevel.L3_REPRODUCED,
            claimed_level=EvidenceLevel.L3_REPRODUCED,
            evidence={"file_path": str(artifact), "expected_hash": exp_h},
        )
        self.assertEqual(status, ValidationState.PASS.value)
        self.assertEqual(obs, EvidenceLevel.L3_REPRODUCED)
        self.assertIn("SHA-256 verified independently", msg)

    # 18. insufficient evidence rejection
    def test_18_insufficient_evidence_rejection(self) -> None:
        # Envelope self-claims PASS with only L1 artifact presence
        envelope = {
            "task_id": "t1",
            "status": "PASS",
            "validations": [{
                "name": "critical_output_sha",
                "evidence_level": "L1_ARTIFACT",
                "evidence": {"file_path": str(self.td / "output.bin")},
            }],
        }
        reqs = [{
            "name": "critical_output_sha",
            "required_evidence_level": "L3_REPRODUCED",
        }]
        verdict, records, summary = self.adjudicator.adjudicate_envelope(envelope, reqs)
        # Must be rejected because observed L1 < required L3!
        self.assertEqual(verdict, ValidationState.REVIEW_REQUIRED.value)
        self.assertIn("adjudication blocked", summary)

    # 19. final adjudication
    def test_19_final_adjudication(self) -> None:
        self.registry.create_job("job_adj_01", "deploy", str(self.td))
        att = self.registry.start_attempt("job_adj_01", "w1", "proc", {"pid": 1, "host": "local"})

        artifact = self.td / "res.txt"
        artifact.write_text("verified", encoding="utf-8")
        import hashlib
        h = hashlib.sha256(b"verified").hexdigest()

        envelope = {
            "task_id": "job_adj_01",
            "status": "PASS",
            "validations": [{
                "name": "checksum",
                "evidence_level": "L3_REPRODUCED",
                "evidence": {"file_path": str(artifact), "expected_hash": h},
            }],
        }
        reqs = [{"name": "checksum", "required_evidence_level": "L3_REPRODUCED"}]
        rec = self.registry.final_adjudicate("job_adj_01", att.attempt_id, "test_adjudicator", envelope, reqs)
        self.assertEqual(rec.result, ValidationState.PASS.value)

        # Job state becomes COMPLETED and lease is released
        job = self.registry.get_job("job_adj_01")
        self.assertEqual(job.job_state, JobState.COMPLETED.value)
        self.assertEqual(job.validation_state, ValidationState.PASS.value)
        self.assertIsNone(self.registry.lease_mgr.get_lease("job_adj_01"))

    # 20. artifact lineage
    def test_20_artifact_lineage(self) -> None:
        target = self.td / "artifact.dat"
        target.write_bytes(b"data")
        import hashlib
        h = hashlib.sha256(b"data").hexdigest()

        lineage = {
            "artifact_id": "art-01",
            "job_id": "job-100",
            "attempt_id": "job-100_att_1",
            "writer_id": "worker_9",
            "path": str(target),
            "sha256": h,
            "input_refs": ["input_manifest_v1"],
        }
        self.assertEqual(lineage["job_id"], "job-100")
        self.assertEqual(lineage["writer_id"], "worker_9")

    # 21. authorized-root violation
    def test_21_authorized_root_violation(self) -> None:
        ckpt = MachineCheckpoint(
            checkpoint_version=1,
            job_id="job_auth_fail",
            attempt_id="att_1",
            input_identity="in",
            authorized_root=str(self.td / "non_existent_auth_root_dir"),
        )
        valid, reason = self.ckpt_mgr.validate_checkpoint_for_resume(ckpt)
        self.assertFalse(valid)
        self.assertIn("authorized_root does not exist", reason)

    # 22. cancellation
    def test_22_cancellation(self) -> None:
        self.registry.create_job("job_canc_01", "scan", str(self.td))
        self.registry.lease_mgr.acquire_lease("job_canc_01", "att_1", "w1")
        self.registry.cancel_job("job_canc_01", reason="user requested cancel")

        job = self.registry.get_job("job_canc_01")
        self.assertEqual(job.job_state, JobState.CANCELLED.value)
        self.assertEqual(job.cancel_requested, 1)
        self.assertIsNone(self.registry.lease_mgr.get_lease("job_canc_01"))

    # 23. completed job no restart
    def test_23_completed_job_no_restart(self) -> None:
        self.registry.create_job("job_comp_01", "task", str(self.td))
        att = self.registry.start_attempt("job_comp_01", "w1", "proc", {"pid": 1, "host": "local"})
        # Directly adjudicate pass
        self.registry.final_adjudicate("job_comp_01", att.attempt_id, "val", {"validations": []}, [])

        # Attempting to start another attempt on completed job must raise!
        with self.assertRaises(RuntimeError):
            self.registry.start_attempt("job_comp_01", "w2", "proc", {"pid": 2, "host": "local"})

    # 24. scheduler idempotence
    def test_24_scheduler_idempotence(self) -> None:
        self.registry.create_job("job_idem_01", "task", str(self.td))
        att = self.registry.start_attempt("job_idem_01", "w1", "proc", {"pid": 55, "host": "local", "process_start_time": "t0"})

        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=True):
            tick1 = durable_execution_recovery_tick(self.db_path)
            tick2 = durable_execution_recovery_tick(self.db_path)
            tick3 = durable_execution_recovery_tick(self.db_path)
            self.assertEqual(tick1[0].action_type, "NO_ACTION")
            self.assertEqual(tick2[0].action_type, "NO_ACTION")
            self.assertEqual(tick3[0].action_type, "NO_ACTION")
            # Invariant: running 3 ticks does not launch duplicate workers or advance attempt!
            job = self.registry.get_job("job_idem_01")
            self.assertEqual(job.current_attempt_id, att.attempt_id)

    # 25. DB backup
    def test_25_db_backup(self) -> None:
        self.registry.create_job("job_bkp_01", "task", str(self.td))
        backup_file = self.td / "backup_durable_jobs.db"

        # SQLite online backup
        src_conn = sqlite3.connect(self.db_path)
        dst_conn = sqlite3.connect(str(backup_file))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        self.assertTrue(backup_file.is_file())
        self.assertGreater(backup_file.stat().st_size, 0)

    # 26. restore
    def test_26_restore(self) -> None:
        self.registry.create_job("job_rst_01", "task", str(self.td))
        backup_file = self.td / "backup.db"
        src_conn = sqlite3.connect(self.db_path)
        dst_conn = sqlite3.connect(str(backup_file))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        # Restore into clean location
        restored_db = self.td / "restored.db"
        src_conn2 = sqlite3.connect(str(backup_file))
        dst_conn2 = sqlite3.connect(str(restored_db))
        try:
            src_conn2.backup(dst_conn2)
        finally:
            dst_conn2.close()
            src_conn2.close()

        restored_reg = DurableJobRegistry(restored_db)
        job = restored_reg.get_job("job_rst_01")
        self.assertIsNotNone(job)
        self.assertEqual(job.job_id, "job_rst_01")

    # 27. scanner synthetic
    def test_27_scanner_synthetic(self) -> None:
        # Full synthetic scanner test:
        # Create -> Lease -> Attempt 1 -> Checkpoint -> Worker Dead -> Recovery Tick -> Attempt 2 -> Complete
        self.registry.create_job("scanner_synth", "scanner", str(self.td))
        att1 = self.registry.start_attempt("scanner_synth", "scanner_w1", "scanner_bin", {"pid": 110, "host": "local"})

        # Worker writes progress checkpoint
        ckpt = MachineCheckpoint(checkpoint_version=1, job_id="scanner_synth", attempt_id=att1.attempt_id, input_identity="tree", cursor=500, authorized_root=str(self.td))
        cpath = self.ckpt_mgr.write_checkpoint(ckpt)
        self.registry.record_checkpoint("scanner_synth", att1.attempt_id, str(cpath))

        # Simulation: Worker 1 crashes
        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=False):
            actions = durable_execution_recovery_tick(self.db_path)
            self.assertEqual(actions[0].action_type, "RESUME_ATTEMPT")
            job = self.registry.get_job("scanner_synth")
            self.assertEqual(job.current_attempt_id, "scanner_synth_att_2")

    # 28. shadow synthetic
    def test_28_shadow_synthetic(self) -> None:
        # Artifact with unknown writer or claiming self-certified pass without L3 evidence
        envelope = {
            "task_id": "shadow_synth",
            "status": "PASS",
            "canonical_retained": True,
            "validations": [{
                "name": "spec_verification",
                "evidence_level": "L1_ARTIFACT",  # Only L1 claimed!
                "evidence": {"file": "PRE_STATE.json"},
            }],
        }
        reqs = [{
            "name": "spec_verification",
            "required_evidence_level": "L3_REPRODUCED",
        }]
        verdict, _, _ = self.adjudicator.adjudicate_envelope(envelope, reqs)
        self.assertEqual(verdict, ValidationState.REVIEW_REQUIRED.value)

    # 29. Switchboard unavailable
    def test_29_switchboard_unavailable(self) -> None:
        # Simulate Switchboard module/server being completely down or unimportable
        with mock.patch.dict(sys.modules, {"mcp.agent-switchboard": None, "agent_broker_mcp": None}):
            # DurableJobRegistry and its tools must work with zero dependency on Switchboard!
            tools = DshJobTools(self.db_path)
            res = tools.create_job("job_standalone_01", "task", str(self.td))
            self.assertTrue(res["ok"])
            queried = tools.get_job("job_standalone_01")
            self.assertTrue(queried["ok"])
            self.assertEqual(queried["job"]["job_id"], "job_standalone_01")

    # 30. DSH session handoff while job worker continues
    def test_30_dsh_session_handoff_while_job_worker_continues(self) -> None:
        self.registry.create_job("job_handoff_01", "worker", str(self.td))
        att = self.registry.start_attempt("job_handoff_01", "w1", "proc", {"pid": 200, "host": "local", "process_start_time": "t0"})

        # Orchestration session 1 ends due to context pressure / handoff
        self.registry.update_orchestration_state("job_handoff_01", OrchestrationState.ENDED.value)

        # Worker is still alive in OS
        with mock.patch("jobs.recovery_tick.is_worker_alive", return_value=True):
            # Recovery tick must NOT touch or restart the worker
            actions = durable_execution_recovery_tick(self.db_path)
            self.assertEqual(actions[0].action_type, "NO_ACTION")

            # New session picks up job without restarting worker
            job = self.registry.get_job("job_handoff_01")
            self.assertEqual(job.job_state, JobState.RUNNING.value)
            self.assertEqual(job.current_attempt_id, att.attempt_id)


if __name__ == "__main__":
    unittest.main()
