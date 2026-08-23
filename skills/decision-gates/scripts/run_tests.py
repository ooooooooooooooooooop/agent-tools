#!/usr/bin/env python3
"""Regression tests for the decision-gates zero-token gate scripts.

Runs with the Python standard library only. Covers the PASS/FAIL branches of
gate_consistency, gate_cost and gate_selfcheck. Exit code 0 = all pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run_script(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
    )


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_consistency_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        write_json(report, {
            "phase_id": "p-001",
            "target_files": ["a.py", "b.py"],
            "workers": [
                {"worker_id": "w1", "files": [
                    {"path": "a.py", "conclusion": "PASS", "sha256_after": "aa"},
                ]},
                {"worker_id": "w2", "files": [
                    {"path": "b.py", "conclusion": "PASS", "sha256_after": "bb"},
                ]},
            ],
        })
        result = run_script("gate_consistency.py", str(report))
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"verdict": "PASS"' in result.stdout, result.stdout


def test_consistency_conflict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        write_json(report, {
            "phase_id": "p-002",
            "target_files": ["a.py"],
            "workers": [
                {"worker_id": "w1", "files": [
                    {"path": "a.py", "conclusion": "PASS", "sha256_after": "aa"},
                ]},
                {"worker_id": "w2", "files": [
                    {"path": "a.py", "conclusion": "FAIL", "sha256_after": "bb"},
                ]},
            ],
        })
        result = run_script("gate_consistency.py", str(report))
        assert result.returncode == 1, result.stdout + result.stderr
        assert '"verdict": "CONFLICT"' in result.stdout, result.stdout


def test_consistency_skip_surfaced() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        write_json(report, {
            "phase_id": "p-003",
            "target_files": ["a.py", "b.py"],
            "workers": [
                {"worker_id": "w1", "files": [
                    {"path": "a.py", "conclusion": "PASS", "sha256_after": "aa"},
                ]},
                {"worker_id": "w2", "files": [
                    {"path": "b.py", "conclusion": "SKIP", "reason": "ambiguous pattern"},
                ]},
            ],
        })
        result = run_script("gate_consistency.py", str(report))
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"verdict": "WARN"' in result.stdout, result.stdout
        assert "unresolved_marker" in result.stdout, result.stdout


def test_cost_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "cost.json"
        write_json(report, {
            "dispatch_id": "d-001",
            "authorized": {"tier": "luna", "model": "gpt-5.6-luna-max", "budget_tokens": 5000},
            "actual": {"tier": "luna", "model": "gpt-5.6-luna-max", "tokens": 5500},
        })
        result = run_script("gate_cost.py", str(report))
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"verdict": "OK"' in result.stdout, result.stdout


def test_cost_deviation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "cost.json"
        write_json(report, {
            "dispatch_id": "d-002",
            "authorized": {"tier": "luna", "model": "gpt-5.6-luna-max", "budget_tokens": 5000},
            "actual": {"tier": "sol", "model": "gpt-5.6-sol-max", "tokens": 12000},
        })
        result = run_script("gate_cost.py", str(report))
        assert result.returncode == 1, result.stdout + result.stderr
        assert '"verdict": "COST_DEVIATION"' in result.stdout, result.stdout
        assert "tier drift" in result.stdout, result.stdout


def test_selfcheck_clean() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        write_json(directory / "cp-001.json", {
            "checkpoint_id": "cp-001",
            "alignment_decision": "PASS",
            "evidence": [
                {"type": "command_output", "cmd": "pytest -q", "exit_code": 0},
            ],
        })
        result = run_script("gate_selfcheck.py", str(directory))
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"verdict": "PASS"' in result.stdout, result.stdout


def test_selfcheck_bias() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        write_json(directory / "cp-001.json", {
            "checkpoint_id": "cp-001",
            "alignment_decision": "PASS",
            "evidence": [
                {"type": "command_output", "cmd": "pytest -q", "exit_code": 1},
            ],
        })
        write_json(directory / "cp-002.json", {
            "checkpoint_id": "cp-002",
            "alignment_decision": "PASS",
            "evidence": [
                {"conclusion": "SKIP", "reason": "ambiguous"},
            ],
        })
        result = run_script("gate_selfcheck.py", str(directory))
        assert result.returncode == 1, result.stdout + result.stderr
        assert '"verdict": "DEFENSE_DRIFT"' in result.stdout, result.stdout
        assert '"summary_bias_count": 2' in result.stdout, result.stdout


def test_scope_lock_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "scope.json"
        write_json(report, {
            "allowed_patterns": ["src/auth/*", "tests/*"],
            "forbidden_patterns": ["config/*", "package.json"],
            "changed_files": ["src/auth/login.ts", "tests/login.test.ts"],
        })
        result = run_script("gate_scope_lock.py", str(report))
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"verdict": "PASS"' in result.stdout, result.stdout


def test_scope_lock_violation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "scope.json"
        write_json(report, {
            "allowed_patterns": ["src/auth/*"],
            "forbidden_patterns": ["config/*"],
            "changed_files": ["src/auth/login.ts", "config/database.yml"],
        })
        result = run_script("gate_scope_lock.py", str(report))
        assert result.returncode == 1, result.stdout + result.stderr
        assert '"verdict": "SCOPE_VIOLATION"' in result.stdout, result.stdout
        assert "MATCHES_FORBIDDEN_PATTERN" in result.stdout, result.stdout


def test_deadband_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "trajectory.json"
        write_json(report, {
            "trajectory": [
                {"tool": "edit", "target": "a.py", "exit_code": 1, "error_signature": "SyntaxError"},
                {"tool": "edit", "target": "a.py", "exit_code": 0, "error_signature": ""},
            ],
        })
        result = run_script("gate_deadband.py", str(report))
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"verdict": "PASS"' in result.stdout, result.stdout


def test_deadband_tripped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "trajectory.json"
        write_json(report, {
            "max_consecutive_failures": 2,
            "trajectory": [
                {"tool": "edit", "target": "a.py", "exit_code": 1, "error_signature": "TypeError: null"},
                {"tool": "edit", "target": "a.py", "exit_code": 1, "error_signature": "TypeError: null"},
            ],
        })
        result = run_script("gate_deadband.py", str(report))
        assert result.returncode == 1, result.stdout + result.stderr
        assert '"verdict": "DEADBAND_TRIPPED"' in result.stdout, result.stdout
        assert "PIVOT_HYPOTHESIS" in result.stdout, result.stdout


def main() -> int:
    tests = [
        test_consistency_pass,
        test_consistency_conflict,
        test_consistency_skip_surfaced,
        test_cost_ok,
        test_cost_deviation,
        test_selfcheck_clean,
        test_selfcheck_bias,
        test_scope_lock_pass,
        test_scope_lock_violation,
        test_deadband_pass,
        test_deadband_tripped,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL: {test.__name__}: {exc}")
        else:
            print(f"PASS: {test.__name__}")
    if failures:
        print(f"{failures} test(s) failed")
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
