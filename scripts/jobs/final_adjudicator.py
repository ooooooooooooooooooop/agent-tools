"""final_adjudicator.py — Sole authoritative final adjudication and L0-L4 evidence enforcement."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import EvidenceLevel, ValidationState


class FinalAdjudicationError(RuntimeError):
    """Raised when an invalid entity attempts to adjudicate or bypass evidence gates."""


def compute_file_sha256(path: Path) -> str:
    """Compute sha256 hash of a file on disk."""
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class FinalAdjudicator:
    """The sole authoritative gatekeeper that evaluates evidence and grants VALIDATION_STATE=PASS."""

    def __init__(self) -> None:
        pass

    def evaluate_requirement(
        self,
        requirement_name: str,
        required_level: EvidenceLevel,
        claimed_level: EvidenceLevel,
        evidence: Dict[str, Any],
    ) -> Tuple[str, EvidenceLevel, str]:
        """Evaluate an individual validation requirement against evidence.

        Returns:
            (result_status, observed_level, detail_message)
        """
        # Rule 1: Claimed level alone is at most L0 or L1
        if claimed_level < required_level:
            return (
                ValidationState.REVIEW_REQUIRED.value,
                claimed_level,
                f"insufficient evidence level for '{requirement_name}': required {required_level.name}, claimed {claimed_level.name}",
            )

        # Rule 2: If required is L3_REPRODUCED (e.g. SHA-256 verification or test pass)
        if required_level >= EvidenceLevel.L3_REPRODUCED:
            file_path = evidence.get("file_path")
            expected_hash = evidence.get("expected_hash")
            verify_cmd = evidence.get("verify_command")

            if file_path and expected_hash:
                p = Path(file_path)
                if not p.is_file():
                    return ValidationState.FAIL.value, EvidenceLevel.L1_ARTIFACT, f"target file does not exist: {file_path}"
                actual_hash = compute_file_sha256(p)
                if actual_hash.lower() != str(expected_hash).lower():
                    return (
                        ValidationState.FAIL.value,
                        EvidenceLevel.L3_REPRODUCED,
                        f"hash mismatch for '{file_path}': expected {expected_hash}, calculated {actual_hash}",
                    )
                # Independent re-calculation succeeded!
                return ValidationState.PASS.value, EvidenceLevel.L3_REPRODUCED, f"SHA-256 verified independently: {actual_hash}"

            if verify_cmd:
                try:
                    res = subprocess.run(
                        verify_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=evidence.get("timeout_sec", 60),
                    )
                    if res.returncode != 0:
                        return (
                            ValidationState.FAIL.value,
                            EvidenceLevel.L3_REPRODUCED,
                            f"verification command failed with exit code {res.returncode}: {res.stderr.strip()[:300]}",
                        )
                    return ValidationState.PASS.value, EvidenceLevel.L3_REPRODUCED, "verification command exited 0"
                except Exception as exc:
                    return ValidationState.FAIL.value, EvidenceLevel.L3_REPRODUCED, f"verification command exception: {exc}"

            # If evidence has no reproducible derivation
            return (
                ValidationState.REVIEW_REQUIRED.value,
                EvidenceLevel.L1_ARTIFACT,
                f"requirement '{requirement_name}' requires L3 reproducibility, but no verifiable command or hash was provided",
            )

        # Rule 3: If required is L1_ARTIFACT
        if required_level == EvidenceLevel.L1_ARTIFACT:
            file_path = evidence.get("file_path")
            if file_path and not Path(file_path).exists():
                return ValidationState.FAIL.value, EvidenceLevel.L0_CLAIM, f"artifact does not exist: {file_path}"
            return ValidationState.PASS.value, EvidenceLevel.L1_ARTIFACT, "artifact verified present"

        return ValidationState.PASS.value, claimed_level, "claim accepted"

    def adjudicate_envelope(
        self,
        envelope_data: Dict[str, Any],
        required_validations: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[str, List[Dict[str, Any]], str]:
        """Adjudicate a completed ResultEnvelope before admitting job completion.

        Self-certification prevention:
        If envelope_data['status'] claims 'PASS', but validations do not satisfy required evidence levels,
        the status is strictly downgraded to REVIEW_REQUIRED or FAIL.
        """
        reqs = required_validations or []
        records = []
        overall_pass = True

        for req in reqs:
            name = req["name"]
            req_lvl = EvidenceLevel.from_str(req.get("required_evidence_level", "L3_REPRODUCED"))
            # Check if envelope contains validation evidence
            matching_v = next((v for v in envelope_data.get("validations", []) if v.get("name") == name), None)

            if matching_v is None:
                records.append({
                    "name": name,
                    "result": ValidationState.FAIL.value,
                    "observed_level": "L0_CLAIM",
                    "required_level": req_lvl.name,
                    "reason": "missing required validation in envelope",
                })
                overall_pass = False
                continue

            claimed_str = matching_v.get("evidence_level", "L1_ARTIFACT")
            claimed_lvl = EvidenceLevel.from_str(claimed_str)

            status, observed_lvl, detail = self.evaluate_requirement(
                name, req_lvl, claimed_lvl, matching_v.get("evidence", {})
            )
            records.append({
                "name": name,
                "result": status,
                "observed_level": observed_lvl.name,
                "required_level": req_lvl.name,
                "reason": detail,
            })
            if status != ValidationState.PASS.value:
                overall_pass = False

        if not overall_pass:
            return ValidationState.REVIEW_REQUIRED.value, records, "adjudication blocked: one or more requirements failed evidence gate"

        return ValidationState.PASS.value, records, "adjudication passed: all evidence criteria satisfied"
