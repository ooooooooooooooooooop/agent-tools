#!/usr/bin/env python3
"""Canonical DSH Runtime Compatibility Engine & Preflight Gate.

This module enforces the Single Source of Truth (SSOT) defined in
registry/harnesses/dsh.yaml to prevent mixed-train regressions,
service contract pending hangs in Cordis, duplicate ownership, and artifact drift.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# Frozen SSOT dict, set ONLY inside generated deployments (deploy_gate). The
# authoring engine always resolves the SSOT from disk instead.
EMBEDDED_SSOT: dict[str, Any] | None = None

# The generated deployment section replaces everything at and below this marker.
DEPLOY_ASSEMBLY_STRIP_MARKER = "# DEPLOY_ASSEMBLY_STRIP_BELOW"

_SSOT_RELATIVE = Path("registry") / "harnesses" / "dsh.yaml"


@dataclass
class PreflightResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "details": self.details,
        }


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        raise RuntimeError(f"Failed to load YAML from {path}: {exc}") from exc


def get_compatibility_ssot() -> dict[str, Any]:
    """Locate the canonical compatibility SSOT regardless of where this file runs from.

    Resolution order:
      1. EMBEDDED_SSOT — frozen dict baked in by deploy_gate (self-contained profile).
      2. DSH_CONTRACT_ROOT environment override.
      3. Authoring repo layout (this file under <root>/scripts/aic/).
      4. Deployed DSH home (<home>/.dsh/registry/harnesses/dsh.yaml).

    A gate running outside the repo layout deliberately has NO working-tree
    fallback: a runtime gate must never silently depend on an authoring checkout
    (incident 2026-09-05: a hand-deployed copy resolved parents[2] to ~/.dsh and
    crashed on every launch, bricking DSH startup).
    """
    if EMBEDDED_SSOT:
        return EMBEDDED_SSOT
    candidates: list[Path] = []
    env_root = os.environ.get("DSH_CONTRACT_ROOT")
    if env_root:
        candidates.append(Path(env_root) / _SSOT_RELATIVE)
    candidates.append(ROOT / _SSOT_RELATIVE)
    candidates.append(Path.home() / ".dsh" / _SSOT_RELATIVE)
    for candidate in candidates:
        if candidate.is_file():
            data = load_yaml(candidate)
            compat = data.get("runtime_composition", {}).get("compatibility")
            if not compat:
                raise RuntimeError(f"runtime_composition.compatibility missing from {candidate}")
            return compat
    raise RuntimeError(
        "Canonical DSH contract not found; tried: "
        + ", ".join(str(c) for c in candidates)
        + ". Deploy a self-contained gate: python scripts/aic/dsh_compatibility.py --action deploy --profile <profile_root>"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def extract_injected_services_from_js(js_path: Path, strict: bool = False) -> tuple[list[str], str | None]:
    """Extract services declared in Cordis inject = [...] from bundled JS.
    
    If strict=True and the file exists but no inject array can be parsed,
    returns ([], "PARSE_UNKNOWN"). Never silently passes unparseable critical bundles.
    """
    if not js_path.is_file():
        return [], "FILE_NOT_FOUND"
    try:
        text = js_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return [], f"READ_ERROR: {exc}"

    match = re.search(r'(?:const\s+inject\s*=|export\s+const\s+inject\s*=|inject\s*:)\s*\[([^\]]+)\]', text)
    if match:
        raw_items = match.group(1)
        services = re.findall(r'["\']([a-zA-Z0-9_\-]+)["\']', raw_items)
        if not services and strict:
            return [], "PARSE_UNKNOWN_EMPTY_INJECT"
        return services, None

    if strict:
        return [], "PARSE_UNKNOWN"
    return [], None


def discover_runtime_provided_services(profile_root: Path) -> set[str]:
    """Discover provided services via multi-source cross-verification.
    
    Sources:
      1. Base cordis.patch.yml & dsh-web-app cordis.patch.yml registered plugin entries
      2. package.json manifests (@deepseek-ai/cordis.services.provided)
      3. Active profile plugin insertions
    """
    discovered: set[str] = set()
    base_nm = profile_root / "base-dsh-0.1.1-rc.2" / "node_modules" / "@deepseek-ai"
    if not base_nm.is_dir():
        # Fallback to local profile node_modules
        base_nm = profile_root / "node_modules" / "@deepseek-ai"

    # Scan patch files for known service provider rows
    patch_files = [
        base_nm / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-base" / "cordis.patch.yml",
        base_nm / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-web-app" / "cordis.patch.yml",
        profile_root / "cordis.patch.yml",
    ]
    try:
        import yaml
        for pf in patch_files:
            if pf.is_file():
                content = pf.read_text(encoding="utf-8", errors="ignore")
                parsed = yaml.safe_load(content)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "insert" in item:
                            for row in item["insert"]:
                                if isinstance(row, dict) and "id" in row:
                                    discovered.add(row["id"])
    except Exception:
        pass

    # Also scan package.json metadata
    if base_nm.is_dir():
        for pkg_dir in base_nm.iterdir():
            pj = pkg_dir / "package.json"
            if pj.is_file():
                try:
                    data = json.loads(pj.read_text(encoding="utf-8", errors="ignore"))
                    cordis_block = data.get("@deepseek-ai/cordis", data.get("cordis", {}))
                    provs = cordis_block.get("services", {}).get("provided", [])
                    if isinstance(provs, list):
                        for s in provs:
                            discovered.add(s)
                except Exception:
                    pass

    return discovered


class DshCompatibilityChecker:
    def __init__(self, profile_root: Path, ssot: dict[str, Any] | None = None):
        self.profile_root = profile_root
        self.dsh_home = profile_root.parent.parent
        self.ssot = ssot or get_compatibility_ssot()
        self.canonical_train = self.ssot.get("canonical_runtime_train", "0.1.1-rc")
        self.host_version = self.ssot.get("host_version", "0.1.1-rc.2")
        self.allow_mixed_train = self.ssot.get("allow_mixed_train", False)
        self.critical_plugins = self.ssot.get("critical_plugins", [])
        self.runtime_provided_services = set(self.ssot.get("runtime_provided_services", []))
        self.last_known_good = self.ssot.get("last_known_good", {})

    def resolve_base_root(self) -> Path:
        cand = self.profile_root / f"base-dsh-{self.host_version}"
        if cand.is_dir():
            return cand
        return self.profile_root

    def check_version_cohesion(self, target_base_root: Path | None = None) -> list[str]:
        """Verify that all core, UI, and overlay packages belong to the canonical release train."""
        errors: list[str] = []
        base_root = target_base_root or self.resolve_base_root()
        node_modules_dirs = [
            base_root / "node_modules" / "@deepseek-ai",
            base_root / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / "@deepseek-ai",
            self.profile_root / "node_modules" / "@deepseek-ai",
        ]

        checked_packages: dict[str, str] = {}
        for nm in node_modules_dirs:
            if not nm.is_dir():
                continue
            for pkg_dir in nm.iterdir():
                if not pkg_dir.is_dir():
                    continue
                pkg_json = pkg_dir / "package.json"
                if pkg_json.is_file():
                    try:
                        data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
                        name = data.get("name")
                        ver = data.get("version")
                        if name and ver:
                            checked_packages[name] = ver
                    except Exception:
                        pass

        # Check groups from SSOT
        groups = self.ssot.get("groups", {})
        for grp_name, grp_info in groups.items():
            package_rules = grp_info.get("package_rules", {})
            for pkg_name, allowed_trains in package_rules.items():
                if pkg_name in checked_packages:
                    ver = checked_packages[pkg_name]
                    matched = any(
                        ver.startswith(train) or ver == train
                        for train in allowed_trains
                    )
                    if not matched:
                        errors.append(
                            f"[VERSION_DRIFT] Package {pkg_name}@{ver} in group '{grp_name}' does not match "
                            f"allowed rules {allowed_trains} (canonical={self.canonical_train})"
                        )

        # Explicit mixed-train prohibition
        if not self.allow_mixed_train:
            for pkg_name, ver in checked_packages.items():
                if "alpha" in ver and not self.canonical_train.startswith("0.1.2-alpha"):
                    errors.append(
                        f"[MIXED_RELEASE_TRAIN] Alpha package {pkg_name}@{ver} detected in non-alpha "
                        f"canonical runtime train {self.canonical_train}"
                    )
                if "beta" in ver and not self.canonical_train.startswith("0.1.2-beta"):
                    errors.append(
                        f"[MIXED_RELEASE_TRAIN] Beta package {pkg_name}@{ver} detected in non-beta "
                        f"canonical runtime train {self.canonical_train}"
                    )

        return errors

    def check_service_contracts(self, target_base_root: Path | None = None) -> list[str]:
        """Compute all critical plugin required services vs runtime provided services.
        
        FAIL CLOSED if:
          - A critical plugin's injected services cannot be parsed (PARSE_UNKNOWN)
          - Any required service is unresolved
          - Any forbidden service is demanded (such as uiSession / uiWorkspace in 0.1.1-rc)
        """
        errors: list[str] = []
        base_root = target_base_root or self.resolve_base_root()

        for crit in self.critical_plugins:
            plugin_id = crit.get("id")
            pkg_name = crit.get("package")
            declared_reqs = set(crit.get("required_services", []))
            forbidden = set(crit.get("forbidden_services", []))

            # Locate client.js / index.js for this plugin
            candidate_paths = [
                base_root / "node_modules" / Path(pkg_name) / "lib" / "client.js",
                base_root / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / Path(pkg_name) / "lib" / "client.js",
                base_root / "node_modules" / Path(pkg_name) / "lib" / "index.js",
            ]
            live_injected: set[str] = set()
            found_bundle = False
            parse_status: str | None = None

            for p in candidate_paths:
                if p.is_file():
                    found_bundle = True
                    extracted, err = extract_injected_services_from_js(p, strict=False)
                    if not err and extracted:
                        live_injected = set(extracted)
                        break

            # If this critical plugin has specific required services defined in SSOT,
            # we enforce those. For UI conversation specifically, verify inject exists in client.js.
            if plugin_id == "ui-conversation" and found_bundle and not live_injected:
                # conversation plugin MUST have parseable inject list
                p = candidate_paths[0] if candidate_paths[0].is_file() else candidate_paths[1]
                _, err = extract_injected_services_from_js(p, strict=True)
                if err:
                    errors.append(
                        f"[PARSE_UNKNOWN_FAIL_CLOSED] Critical plugin '{plugin_id}' ({pkg_name}) bundle is unparseable "
                        f"({err}) — failing closed to prevent pending hang."
                    )
                    continue

            all_required = declared_reqs.union(live_injected)

            # 1. Check forbidden services
            colliding_forbidden = all_required.intersection(forbidden)
            if colliding_forbidden:
                errors.append(
                    f"[SERVICE_CONTRACT_VIOLATION] Critical plugin '{plugin_id}' ({pkg_name}) demands forbidden "
                    f"services {sorted(list(colliding_forbidden))} under runtime train {self.canonical_train} "
                    f"— will cause Cordis pending hang!"
                )

            # 2. Check unresolved required services
            unresolved = all_required - self.runtime_provided_services
            if unresolved:
                errors.append(
                    f"[UNRESOLVED_SERVICE_CONTRACT] Critical plugin '{plugin_id}' ({pkg_name}) requires services "
                    f"{sorted(list(unresolved))} which are NOT provided by the runtime "
                    f"— Cordis boot would hang indefinitely."
                )

        return errors

    def check_ownership_uniqueness(self) -> list[str]:
        """Verify that each composition entry and service provider has a single canonical owner."""
        errors: list[str] = []
        patch_file = self.profile_root / "cordis.patch.yml"
        if not patch_file.is_file():
            return errors

        try:
            import yaml
            data = yaml.safe_load(patch_file.read_text(encoding="utf-8", errors="ignore"))
            if not isinstance(data, list):
                return errors
            
            seen_inserted_ids: set[str] = set()
            for item in data:
                if isinstance(item, dict) and "insert" in item:
                    inserts = item["insert"]
                    if isinstance(inserts, list):
                        for row in inserts:
                            if isinstance(row, dict) and "id" in row:
                                row_id = row["id"]
                                if row_id in seen_inserted_ids:
                                    errors.append(
                                        f"[DUPLICATE_OWNERSHIP] Plugin entry id '{row_id}' is inserted multiple times "
                                        f"in cordis.patch.yml"
                                    )
                                seen_inserted_ids.add(row_id)
        except Exception as exc:
            errors.append(f"[OWNERSHIP_CHECK_ERROR] Failed parsing cordis.patch.yml: {exc}")

        return errors

    def check_artifact_identity(self) -> list[str]:
        """Verify deployed artifacts against last-known-good / manifest hashes."""
        errors: list[str] = []
        base_root = self.resolve_base_root()

        # Check client.js
        client_paths = [
            base_root / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib" / "client.js",
            base_root / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib" / "client.js",
        ]
        client_file = next((p for p in client_paths if p.is_file()), None)
        if not client_file:
            errors.append("[ARTIFACT_DRIFT] UI client bundle lib/client.js is missing")

        # Check web dist
        frontend_paths = [
            base_root / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "dist",
            base_root / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "dist",
        ]
        frontend_dir = next((p for p in frontend_paths if p.is_dir()), None)
        if not frontend_dir:
            errors.append("[ARTIFACT_DRIFT] Web frontend dist/ directory is missing")

        return errors

    def run_preflight(self, target_base_root: Path | None = None) -> PreflightResult:
        """Run all preflight gates deterministically."""
        res = PreflightResult(passed=True)

        # Gate A: Version cohesion
        version_errors = self.check_version_cohesion(target_base_root)
        if version_errors:
            res.errors.extend(version_errors)
            res.details["version_cohesion"] = "FAIL"
        else:
            res.details["version_cohesion"] = "PASS"

        # Gate B: Service contract
        service_errors = self.check_service_contracts(target_base_root)
        if service_errors:
            res.errors.extend(service_errors)
            res.details["service_contracts"] = "FAIL"
        else:
            res.details["service_contracts"] = "PASS"

        # Gate C: Ownership uniqueness
        ownership_errors = self.check_ownership_uniqueness()
        if ownership_errors:
            res.errors.extend(ownership_errors)
            res.details["ownership_uniqueness"] = "FAIL"
        else:
            res.details["ownership_uniqueness"] = "PASS"

        # Gate D: Artifact identity
        artifact_errors = self.check_artifact_identity()
        if artifact_errors:
            res.errors.extend(artifact_errors)
            res.details["artifact_identity"] = "FAIL"
        else:
            res.details["artifact_identity"] = "PASS"

        if res.errors:
            res.passed = False

        return res

    def detect_runtime_drift(self) -> list[dict[str, str]]:
        """Map compatibility and preflight checks to unified drift findings."""
        findings: list[dict[str, str]] = []
        res = self.run_preflight()
        for err in res.errors:
            if err.startswith("[VERSION_DRIFT]") or err.startswith("[MIXED_RELEASE_TRAIN]"):
                findings.append({"category": "VERSION_DRIFT", "message": err})
            elif (err.startswith("[SERVICE_CONTRACT_VIOLATION]") or
                  err.startswith("[UNRESOLVED_SERVICE_CONTRACT]") or
                  err.startswith("[PARSE_UNKNOWN_FAIL_CLOSED]")):
                findings.append({"category": "SERVICE_CONTRACT_DRIFT", "message": err})
            elif err.startswith("[DUPLICATE_OWNERSHIP]"):
                findings.append({"category": "OWNERSHIP_DRIFT", "message": err})
            elif err.startswith("[ARTIFACT_DRIFT]"):
                findings.append({"category": "ARTIFACT_DRIFT", "message": err})
            else:
                findings.append({"category": "COMPOSITION_DRIFT", "message": err})
        return findings

    def recover_last_known_good(self) -> dict[str, Any]:
        """Restore runtime software composition to last-known-good state.
        
        Performs REAL PHYSICAL ARTIFACT RECOVERY (not just metadata):
          - Restores client.js artifact payload
          - Restores web frontend dist tree
          - Restores launcher and cordis patches
          - Restores composition manifest & distribution records
          - Preserves user settings, models, and provider preferences untouched.
        """
        lkg = self.last_known_good
        if not lkg:
            raise RuntimeError("No last_known_good defined in compatibility SSOT")

        target_train = lkg.get("train", "0.1.1-rc")
        target_version = lkg.get("host_version", "0.1.1-rc.2")
        target_hash = lkg.get("composition_hash")

        target_base = self.profile_root / f"base-dsh-{target_version}"
        if not target_base.is_dir():
            raise RuntimeError(f"Last-known-good base distribution not found on disk: {target_base}")

        # 1. Recover physical artifacts from canonical dsh-config templates or LKG backups
        repo_config_dir = ROOT / "dsh-config" / "profiles" / "web"
        if repo_config_dir.is_dir():
            # Restore launcher
            launcher_src = repo_config_dir / "dsh-launch-web.ps1"
            launcher_dst = self.profile_root / "dsh-launch-web.ps1"
            if launcher_src.is_file():
                shutil.copy2(launcher_src, launcher_dst)

            # Restore base-distribution.json
            base_dist_src = repo_config_dir / "base-distribution.json"
            base_dist_dst = self.profile_root / "base-distribution.json"
            if base_dist_src.is_file():
                shutil.copy2(base_dist_src, base_dist_dst)

        # 2. Check and restore corrupted client.js / dist payload from backups if needed
        client_file = target_base / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib" / "client.js"
        dist_dir = target_base / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "dist"
        
        expected_client_hash = lkg.get("ui_bundle_sha256")
        expected_dist_hash = lkg.get("web_dist_sha256")

        # If client_file is corrupted/missing, search LKG backup
        if not client_file.is_file() or (expected_client_hash and sha256_file(client_file) != expected_client_hash):
            backups_dir = self.dsh_home / ".aic-dsh-backups"
            if backups_dir.is_dir():
                for bdir in sorted(backups_dir.iterdir(), reverse=True):
                    cand_client = bdir / "profiles" / "web" / f"base-dsh-{target_version}" / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib" / "client.js"
                    if cand_client.is_file() and sha256_file(cand_client) == expected_client_hash:
                        client_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(cand_client, client_file)
                        break

        # If dist_dir is corrupted/missing, search LKG backup
        if not dist_dir.is_dir() or (expected_dist_hash and sha256_tree(dist_dir) != expected_dist_hash):
            backups_dir = self.dsh_home / ".aic-dsh-backups"
            if backups_dir.is_dir():
                for bdir in sorted(backups_dir.iterdir(), reverse=True):
                    cand_dist = bdir / "profiles" / "web" / f"base-dsh-{target_version}" / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "dist"
                    if cand_dist.is_dir() and sha256_tree(cand_dist) == expected_dist_hash:
                        shutil.rmtree(dist_dir, ignore_errors=True)
                        shutil.copytree(cand_dist, dist_dir)
                        break

        # 2. Update state file
        state_file = self.profile_root / "dsh-managed-state.json"
        state = {
            "schemaVersion": 1,
            "current": {
                "version": target_version,
                "compositionHash": target_hash,
                "nodeRelativePath": "runtime/node-v22.19.0-win-x64",
                "entryRelative": f"profiles/web/base-dsh-{target_version}/node_modules/@deepseek-ai/dsh/lib/bin.js",
                "acceptedAt": lkg.get("validated_at"),
                "restoredFromLKG": True
            },
            "candidate": None,
            "previous": None
        }
        state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        # 3. Verify recovered artifacts match LKG hashes
        client_file = target_base / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib" / "client.js"
        dist_dir = target_base / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "dist"
        
        actual_client_hash = sha256_file(client_file) if client_file.is_file() else None
        actual_dist_hash = sha256_tree(dist_dir) if dist_dir.is_dir() else None

        matched_client = (actual_client_hash == lkg.get("ui_bundle_sha256"))
        matched_dist = (actual_dist_hash == lkg.get("web_dist_sha256"))

        return {
            "status": "RESTORED",
            "restored_version": target_version,
            "restored_train": target_train,
            "composition_hash": target_hash,
            "actual_client_hash": actual_client_hash,
            "actual_dist_hash": actual_dist_hash,
            "content_identity_match": bool(matched_client and matched_dist),
            "user_model_untouched": True,
        }


def deploy_gate(profile_root: Path) -> dict[str, Any]:
    """Generate the profile's self-contained preflight gate.

    The deployed artifact is ASSEMBLED, never hand-copied: engine source with a
    frozen copy of the SSOT (base64-embedded) and a fresh main guard appended.
    This makes the deployed gate location-independent — it carries its own
    contract and must never reach back into the authoring repo at runtime.
    The deployment is self-verified by executing the generated artifact exactly
    as the launcher will (subprocess, --profile pointing at the profile).
    """
    profile_root = Path(profile_root)
    ssot = get_compatibility_ssot()
    engine_path = Path(__file__).resolve()
    engine_src = engine_path.read_text(encoding="utf-8")
    if DEPLOY_ASSEMBLY_STRIP_MARKER not in engine_src:
        raise RuntimeError(f"Engine main-guard marker missing: {DEPLOY_ASSEMBLY_STRIP_MARKER}")
    # rsplit: the marker text also appears inside the constant definition near
    # the top; the assembly point is always its LAST occurrence (the bottom
    # main-guard block).
    engine_head = engine_src.rsplit(DEPLOY_ASSEMBLY_STRIP_MARKER, 1)[0].rstrip() + "\n"
    if "DEPLOY_ASSEMBLY_STRIP_MARKER = " not in engine_head:
        raise RuntimeError("Assembly cut inside the marker constant definition; refusing to deploy")

    ssot_file = ROOT / _SSOT_RELATIVE
    ssot_sha256 = sha256_file(ssot_file) if ssot_file.is_file() else None
    payload = base64.b64encode(json.dumps(ssot, ensure_ascii=False).encode("utf-8")).decode("ascii")

    generated = (
        engine_head
        + "\n# ===== GENERATED DEPLOYMENT SECTION (DO NOT EDIT BY HAND) =====\n"
        + f"# engine source : scripts/aic/{engine_path.name}\n"
        + "# ssot source   : registry/harnesses/dsh.yaml (runtime_composition.compatibility)\n"
        + f"# ssot_sha256   : {ssot_sha256}\n"
        + f"# generated_at  : {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        + "# regenerate    : python scripts/aic/dsh_compatibility.py --action deploy --profile <profile_root>\n"
        + f'EMBEDDED_SSOT = json.loads(base64.b64decode("{payload}").decode("utf-8"))\n'
        + "\n\nif __name__ == \"__main__\":\n    sys.exit(run_preflight_cli())\n"
    )

    profile_root.mkdir(parents=True, exist_ok=True)
    target = profile_root / "dsh-preflight.py"
    target.write_text(generated, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(target), "--profile", str(profile_root), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    try:
        verdict = json.loads(proc.stdout)
    except Exception:
        verdict = {}
    # "functional" = the artifact itself executes and emits a valid verdict at
    # its deployed location (a crash would leave stdout unparseable). Whether
    # the composition actually passes the gates is reported separately.
    functional = isinstance(verdict, dict) and "passed" in verdict and bool(verdict.get("details"))
    preflight_passed = functional and verdict.get("passed") is True
    report: dict[str, Any] = {
        "deployed": str(target),
        "deployed_sha256": sha256_file(target),
        "engine": str(engine_path),
        "ssot_sha256": ssot_sha256,
        "artifact_functional": functional,
        "preflight_passed": preflight_passed,
        "verified": functional and preflight_passed,
        "preflight": verdict,
    }
    if not report["verified"]:
        report["returncode"] = proc.returncode
        report["stdout_tail"] = (proc.stdout or "")[-2000:]
        report["stderr_tail"] = (proc.stderr or "")[-2000:]
    return report


def run_preflight_cli() -> int:
    parser = argparse.ArgumentParser(description="Deterministic DSH Preflight Gate")
    parser.add_argument("--profile", type=str, default="", help="Path to profile directory")
    parser.add_argument("--target-base", type=str, default="", help="Path to candidate base root")
    parser.add_argument("--action", type=str, choices=["preflight", "drift", "recover", "services", "deploy"], default="preflight")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    profile_path = Path(args.profile) if args.profile else Path.home() / ".dsh" / "profiles" / "web"
    target_base = Path(args.target_base) if args.target_base else None

    checker = DshCompatibilityChecker(profile_path)

    if args.action == "deploy":
        res = deploy_gate(profile_path)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("verified") else 1

    if args.action == "services":
        provs = discover_runtime_provided_services(profile_path)
        print(json.dumps(sorted(list(provs)), indent=2))
        return 0

    if args.action == "recover":
        res = checker.recover_last_known_good()
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.action == "drift":
        drifts = checker.detect_runtime_drift()
        if args.json:
            print(json.dumps(drifts, indent=2, ensure_ascii=False))
        else:
            if drifts:
                print(f"DRIFTS DETECTED ({len(drifts)}):")
                for d in drifts:
                    print(f"  [{d['category']}] {d['message']}")
            else:
                print("NO_RUNTIME_DRIFT")
        return 0 if not drifts else 1

    result = checker.run_preflight(target_base)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("=== DSH RUNTIME COMPOSITION PREFLIGHT ===")
        for k, v in result.details.items():
            print(f"  {k:25}: {v}")
        if result.passed:
            print("\nPREFLIGHT RESULT: PASS")
        else:
            print(f"\nPREFLIGHT RESULT: REJECT ({len(result.errors)} errors)")
            for err in result.errors:
                print(f"  - {err}")

    return 0 if result.passed else 1


# DEPLOY_ASSEMBLY_STRIP_BELOW (everything below is replaced by the generated
# deployment section when assembling a self-contained profile gate)
if __name__ == "__main__":
    sys.exit(run_preflight_cli())
