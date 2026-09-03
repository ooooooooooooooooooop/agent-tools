"""test_durability_jobs_backup.py — durable-jobs DB backup coverage + drill guard.

Covers remediation acceptance:
  * durable_jobs.db snapshot via SQLite online backup API (no raw copy)
  * snapshot integrity verified + ledger-recorded
  * restore into isolated temp DB: integrity + schema + unfinished-job enumeration
  * corrupted source DB -> FAIL (never claim success)
  * registration guard rejects ephemeral (%TEMP% / bootstrap-drill) repo roots
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "durability"))

import backup_jobs  # noqa: E402
import backup_sessions  # noqa: E402
from restore_check import check_jobs  # noqa: E402

REGISTER_PS1 = REPO / "scripts" / "governance" / "register_governance_tasks.ps1"


def _make_jobs_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, job_type TEXT, created_at TEXT,"
        " updated_at TEXT, job_state TEXT, orchestration_state TEXT,"
        " validation_state TEXT, authorized_root TEXT, recovery_policy TEXT,"
        " created_by TEXT, cancel_requested INTEGER)"
    )
    con.execute(
        "CREATE TABLE events (job_id TEXT, attempt_id TEXT, timestamp TEXT,"
        " event_type TEXT, payload_json TEXT)"
    )
    con.execute(
        "INSERT INTO jobs VALUES ('j1','sync','t','t','RUNNING','RUNNING','OK',"
        "'/root','auto','tester',0)"
    )
    con.commit()
    con.close()


class JobsBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.src_db = self.td / "jobs" / "durable_jobs.db"
        self.backup_root = self.td / "ai-backup"
        self.state_repo = self.td / "personal-ai-state"
        (self.state_repo / "sync").mkdir(parents=True)
        (self.state_repo / "sync" / "this-device.yaml").write_text(
            f"device_id: TEST\nbackup_root: {self.backup_root}\n", encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {
            "PERSONAL_AI_JOBS_DB": str(self.src_db),
            "PERSONAL_AI_STATE": str(self.state_repo),
        })
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self.temp_dir.cleanup()

    def _ledger(self) -> list:
        led = self.backup_root / "ledger" / "runs.jsonl"
        if not led.is_file():
            return []
        return [json.loads(l) for l in led.read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def test_snapshot_and_ledger(self) -> None:
        _make_jobs_db(self.src_db)
        rc = backup_jobs.main()
        self.assertEqual(rc, 0)
        rows = [r for r in self._ledger() if r.get("dataset") == "jobs"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[0]["integrity_status"], "verified")
        snaps = list((self.backup_root / "jobs").glob("jobs-*.sqlite"))
        self.assertEqual(len(snaps), 1)
        self.assertGreater(snaps[0].stat().st_size, 0)

    def test_restore_fixture_isolated(self) -> None:
        _make_jobs_db(self.src_db)
        self.assertEqual(backup_jobs.main(), 0)
        tmp = self.td / "restore-tmp"
        tmp.mkdir()
        res = check_jobs(self.backup_root, tmp)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["integrity_check"], "ok")
        self.assertTrue(res["schema_ok"])
        self.assertEqual(res["unfinished_jobs"], 1)  # RUNNING job enumerated

    def test_missing_source_fails(self) -> None:
        rc = backup_jobs.main()
        self.assertEqual(rc, 1)
        rows = [r for r in self._ledger() if r.get("dataset") == "jobs"]
        self.assertEqual(rows[0]["status"], "error")

    def test_corrupt_source_fails_not_success(self) -> None:
        self.src_db.parent.mkdir(parents=True, exist_ok=True)
        self.src_db.write_bytes(b"not a sqlite database at all")
        rc = backup_jobs.main()
        self.assertEqual(rc, 1)
        rows = [r for r in self._ledger() if r.get("dataset") == "jobs"]
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(rows[0]["integrity_status"], "failed")

    def test_restore_check_no_snapshot_errors(self) -> None:
        tmp = self.td / "restore-tmp2"
        tmp.mkdir()
        res = check_jobs(self.backup_root, tmp)
        self.assertEqual(res["status"], "error")


class SessionsManifestMergeTests(unittest.TestCase):
    """A later same-day incremental run must not clobber the day's manifest."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.src = self.td / "dsh-sessions"
        self.backup_root = self.td / "ai-backup"
        self.state_repo = self.td / "personal-ai-state"
        (self.state_repo / "sync").mkdir(parents=True)
        (self.state_repo / "sync" / "this-device.yaml").write_text(
            f"device_id: TEST\nbackup_root: {self.backup_root}\n", encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {
            "PERSONAL_AI_SESSIONS_SRC": str(self.src),
            "PERSONAL_AI_STATE": str(self.state_repo),
        })
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self.temp_dir.cleanup()

    def _mk_session(self, rel: str, payload: bytes) -> None:
        f = self.src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(payload)

    def _manifest(self) -> list:
        import datetime
        day = "daily-" + datetime.datetime.now().strftime("%Y-%m-%d")
        m = self.backup_root / "sessions" / day / "manifest.json"
        return json.loads(m.read_text(encoding="utf-8"))["files"]

    def test_second_run_same_day_preserves_manifest(self) -> None:
        self._mk_session("ws-a/s1/session.jsonl.zstd", b"aaa")
        self.assertEqual(backup_sessions.main(), 0)
        self.assertEqual(len(self._manifest()), 1)
        # Second run: nothing changed -> all skipped; manifest must keep the entry.
        self.assertEqual(backup_sessions.main(), 0)
        self.assertEqual(len(self._manifest()), 1)

    def test_second_run_merges_new_files(self) -> None:
        self._mk_session("ws-a/s1/session.jsonl.zstd", b"aaa")
        self.assertEqual(backup_sessions.main(), 0)
        self._mk_session("ws-b/s2/session.jsonl.zstd", b"bbb")
        self.assertEqual(backup_sessions.main(), 0)
        files = sorted(e["file"] for e in self._manifest())
        self.assertEqual(files, ["ws-a/s1/session.jsonl.zstd", "ws-b/s2/session.jsonl.zstd"])


@unittest.skipIf(os.name != "nt", "Task Scheduler guard is Windows-only")
class RegistrationGuardTests(unittest.TestCase):
    """The real register_governance_tasks.ps1 must refuse ephemeral ownership."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_register(self, script_path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script_path), "-CheckOnly"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, env={**os.environ, "TEMP": str(self.td), "TMP": str(self.td)},
        )

    def test_temp_mirror_registration_rejected(self) -> None:
        # Simulate a bootstrap drill: script lives under a %TEMP% mirror tree.
        mirror = self.td / "bootstrap-drill-abc123" / "home" / "Desktop" / "skills" / "scripts" / "governance"
        mirror.mkdir(parents=True)
        script = mirror / "register_governance_tasks.ps1"
        script.write_text(REGISTER_PS1.read_text(encoding="utf-8"), encoding="utf-8")
        p = self._run_register(script)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("REGISTRATION_REJECTED_EPHEMERAL_PATH", p.stdout + p.stderr)

    def test_canonical_registration_accepted_by_guard(self) -> None:
        # From the real canonical checkout the ephemeral guard passes; the
        # device-config anchor then decides (live re-registration covers the
        # full PASS path). Here we only assert the guard does NOT raise
        # REGISTRATION_REJECTED_EPHEMERAL_PATH for a durable path.
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(REGISTER_PS1), "-CheckOnly"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        self.assertNotIn("REGISTRATION_REJECTED_EPHEMERAL_PATH", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
