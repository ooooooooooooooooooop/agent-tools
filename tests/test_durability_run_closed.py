"""tests/test_durability_run_closed.py — regression coverage for closed nightly backup run."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "durability"))

import backup_configs  # noqa: E402
import restore_check  # noqa: E402


class ClosedRunDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.backup_root = self.td / "ai-backup"
        self.state_repo = self.td / "personal-ai-state"
        (self.state_repo / "sync").mkdir(parents=True)
        (self.state_repo / "sync" / "this-device.yaml").write_text(
            f"device_id: TEST\nbackup_root: {self.backup_root}\n", encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {
            "PERSONAL_AI_STATE": str(self.state_repo),
        })
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self.temp_dir.cleanup()

    def test_backup_configs_includes_credentials(self) -> None:
        names = [str(src).replace("\\", "/") for src, _, _ in backup_configs.CONFIGS]
        self.assertTrue(any(".credentials.yaml" in n for n in names),
                        "backup_configs must include .credentials.yaml in protection set")
        entry = next((item for item in backup_configs.CONFIGS if ".credentials.yaml" in str(item[0])), None)
        self.assertIsNotNone(entry)
        _, irreplaceable, secret = entry
        self.assertTrue(irreplaceable, ".credentials.yaml must be marked irreplaceable")
        self.assertTrue(secret, ".credentials.yaml must be marked secret_local_only")

    def test_negative_test_tamper_durable_jobs_fails(self) -> None:
        # Mock minimal valid backup layout
        jobs_dir = self.backup_root / "jobs"
        jobs_dir.mkdir(parents=True)
        db_path = jobs_dir / "jobs-2026-09-06T0000.sqlite"
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, job_type TEXT, created_at TEXT, updated_at TEXT, job_state TEXT, orchestration_state TEXT, validation_state TEXT, authorized_root TEXT, recovery_policy TEXT, created_by TEXT, cancel_requested INTEGER)")
        con.execute("CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, job_id TEXT, writer_id TEXT, worker_type TEXT, worker_identity TEXT, started_at TEXT, ended_at TEXT, worker_state TEXT, exit_code INTEGER, workspace_ref TEXT, result_envelope_ref TEXT, checkpoint_ref TEXT)")
        con.execute("CREATE TABLE leases (job_id TEXT PRIMARY KEY, lease_id TEXT, attempt_id TEXT, writer_id TEXT, acquired_at TEXT, expires_at REAL, last_renewed_at REAL)")
        con.execute("CREATE TABLE events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, attempt_id TEXT, timestamp TEXT, event_type TEXT, payload_json TEXT)")
        con.execute("CREATE TABLE validations (validation_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, attempt_id TEXT, validator_id TEXT, required_evidence_level TEXT, observed_evidence_level TEXT, result TEXT, evidence_refs TEXT, validated_at TEXT)")
        con.commit()
        con.close()

        cfg_dir = self.backup_root / "configs" / "daily-2026-09-06"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / ".dsh--.credentials.yaml").write_text("version: 1\nrefs: {}\nrecords: {}\n", encoding="utf-8")

        res = restore_check.run_isolated_restore_verification(self.backup_root, tamper_remove="durable_jobs")
        self.assertEqual(res["status"], "error")
        self.assertTrue(res["tamper_active"])
        failed_checks = [c for c in res["checks"] if c.get("status") == "error"]
        self.assertTrue(any(c["name"] == "durable_jobs_restore" for c in failed_checks))

    def test_negative_test_tamper_credentials_fails(self) -> None:
        jobs_dir = self.backup_root / "jobs"
        jobs_dir.mkdir(parents=True)
        db_path = jobs_dir / "jobs-2026-09-06T0000.sqlite"
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, job_type TEXT, created_at TEXT, updated_at TEXT, job_state TEXT, orchestration_state TEXT, validation_state TEXT, authorized_root TEXT, recovery_policy TEXT, created_by TEXT, cancel_requested INTEGER)")
        con.execute("CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, job_id TEXT, writer_id TEXT, worker_type TEXT, worker_identity TEXT, started_at TEXT, ended_at TEXT, worker_state TEXT, exit_code INTEGER, workspace_ref TEXT, result_envelope_ref TEXT, checkpoint_ref TEXT)")
        con.execute("CREATE TABLE leases (job_id TEXT PRIMARY KEY, lease_id TEXT, attempt_id TEXT, writer_id TEXT, acquired_at TEXT, expires_at REAL, last_renewed_at REAL)")
        con.execute("CREATE TABLE events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, attempt_id TEXT, timestamp TEXT, event_type TEXT, payload_json TEXT)")
        con.execute("CREATE TABLE validations (validation_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, attempt_id TEXT, validator_id TEXT, required_evidence_level TEXT, observed_evidence_level TEXT, result TEXT, evidence_refs TEXT, validated_at TEXT)")
        con.commit()
        con.close()

        cfg_dir = self.backup_root / "configs" / "daily-2026-09-06"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / ".dsh--.credentials.yaml").write_text("version: 1\nrefs: {}\nrecords: {}\n", encoding="utf-8")

        res = restore_check.run_isolated_restore_verification(self.backup_root, tamper_remove="credentials")
        self.assertEqual(res["status"], "error")
        self.assertTrue(res["tamper_active"])
        failed_checks = [c for c in res["checks"] if c.get("status") == "error"]
        self.assertTrue(any(c["name"] == "credentials_restore" for c in failed_checks))


if __name__ == "__main__":
    unittest.main()
