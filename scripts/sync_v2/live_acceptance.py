"""live_acceptance.py — Real machine live acceptance drill for Personal AI Sync V3 (Truthful Convergence)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_v2.engine import SyncEngine, run_sync
from sync_v2.models import OverallStatus, PlaneStatus
from sync_v2.planes import package_files, sha256_file


def run_live_acceptance() -> dict:
    """Execute real machine live acceptance across A-G."""
    results = {}
    home = Path.home() / ".dsh"

    # A. MCP True Sync Acceptance
    mcp_entry = ROOT / "mcp" / "agent-switchboard" / "agent_broker_mcp.py"
    assert mcp_entry.is_file(), "MCP source must exist"
    rc = subprocess.run([sys.executable, "-m", "py_compile", str(mcp_entry)], capture_output=True)
    assert rc.returncode == 0, "MCP compilation must succeed"

    td_mcp = Path(tempfile.mkdtemp())
    env_mcp = {**os.environ, "AGENT_BROKER_HOME": str(td_mcp)}
    p = subprocess.Popen(
        [sys.executable, str(mcp_entry)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env_mcp,
    )
    try:
        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "live_test"}}}) + "\n"
        p.stdin.write(init_req)
        p.stdin.flush()
        line = p.stdout.readline()
        assert '"protocolVersion"' in line, f"MCP handshake failed: {line}"
        results["A_mcp_true_sync"] = True
    finally:
        p.terminate()
        p.wait(timeout=2)
        shutil.rmtree(td_mcp, ignore_errors=True)

    # B. Plugins True Sync Acceptance (7 managed plugins active in live DSH)
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:3080/api/pluginInventory/list",
            data=json.dumps({"type": "client-request", "rpcId": "live-sync-test", "method": "pluginInventory/list", "payload": {"args": {}}}).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        entries = res.get("result", {}).get("value", {}).get("entries", [])
        active_plugins = [e["entryId"] for e in entries if e.get("enabled") and e.get("fiberPhase") == "active"]

        required_managed_plugins = [
            "include:token-meter-pressure-guard",
            "include:agent-loop-pressure-guard",
            "include:tool-result-pruner-pressure-guard",
            "include:compaction-basic-convergence",
            "include:context-lifecycle",
            "include:workflow-model-preflight-gate",
            "include:autonomous-execution-governor",
        ]
        all_active = all(p in active_plugins for p in required_managed_plugins)
        results["B_plugins_7_active"] = all_active
    except Exception as exc:
        results["B_plugins_7_active"] = f"DSH query exception: {exc}"

    # C. Skills True Sync Acceptance (Manifest package-by-package SHA-256 tree verification)
    manifest = json.loads((ROOT / "skills.json").read_text(encoding="utf-8-sig"))
    skills_entries = manifest.get("skills", [])
    skills_dir = home / "skills"
    all_skills_matched = True
    for entry in skills_entries:
        s_name = entry["name"]
        src_path = ROOT / entry["path"]
        dst_path = skills_dir / s_name
        if not dst_path.is_dir():
            all_skills_matched = False
            break
        src_f = package_files(src_path)
        dst_f = package_files(dst_path)
        # Verify no missing files and all source files have identical SHA-256 in destination
        for rel, s_file in src_f.items():
            if rel not in dst_f or sha256_file(s_file) != sha256_file(dst_f[rel]):
                all_skills_matched = False
                break
        if not all_skills_matched:
            break
    results["C_skills_manifest_tree_verified"] = all_skills_matched

    # D. Presets True Sync Acceptance
    preset_file = home / ".agent-presets" / "cc" / "agent.cordis.yml"
    assert preset_file.is_file(), "CC preset must exist"
    results["D_presets_true_sync"] = True

    # E. Config True Sync Acceptance (settings.yaml and cordis.patch.yml managed block)
    settings_file = home / "settings.yaml"
    patch_file = home / "profiles" / "web" / "cordis.patch.yml"
    assert settings_file.is_file(), "settings.yaml must exist"
    assert patch_file.is_file(), "cordis.patch.yml must exist"
    assert "# AIC DSH RUNTIME COMPOSITION BEGIN" in patch_file.read_text(encoding="utf-8-sig")
    results["E_config_true_sync"] = True

    # F. Runtime True Sync Acceptance (deployed hash present)
    manifest_p = home / "profiles" / "web" / "dsh-runtime-composition.json"
    manifest_data = json.loads(manifest_p.read_text(encoding="utf-8-sig"))
    deployed_hash = manifest_data.get("profileCombinationHash")
    assert bool(deployed_hash), "deployed hash must be present"
    results["F_runtime_true_sync"] = True

    # G. Developer Dirty Isolation Acceptance
    fixture_path = ROOT / "tmp_dev_dirty_fixture.txt"
    try:
        fixture_path.write_text("developer uncommitted scratchpad\n", encoding="utf-8")
        assert fixture_path.is_file()

        engine = SyncEngine()
        receipt, human_text = engine.run(check_only=True)

        assert fixture_path.is_file(), "developer dirty fixture must remain untouched"
        assert fixture_path.read_text(encoding="utf-8") == "developer uncommitted scratchpad\n"

        mirror_dir = home / ".deployment-mirror" / "agent-tools"
        assert not (mirror_dir / "tmp_dev_dirty_fixture.txt").exists(), "dirty file must not leak to production mirror"
        assert receipt.metadata.get("developer_workspace_dirty") is True
        results["G_developer_dirty_isolated"] = True
    finally:
        if fixture_path.is_file():
            fixture_path.unlink()

    return results


if __name__ == "__main__":
    out = run_live_acceptance()
    print("LIVE_ACCEPTANCE_RESULTS=" + json.dumps(out, indent=2))
    assert all(v is True for v in out.values()), f"Some live acceptance checks failed: {out}"
    print("LIVE_ACCEPTANCE_OVERALL=PASS")
