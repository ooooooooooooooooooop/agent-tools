"""State-matrix tests for AIC's DSH UI source provenance contract."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "aic"))

import dsh_runtime  # noqa: E402
import aic  # noqa: E402


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True)
    return proc.stdout.strip()


class DshSourceStateMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sha1 = "1" * 64
        self.sha2 = "2" * 64
        self.base = {"baseline_commit": "0" * 40,
                     "fix_commit": "f" * 40,
                     "fix_commit_remote": "unavailable-at-audit",
                     "apply_patch": False,
                     "patch_sha256": self.sha1,
                     "build_patch_sha256": self.sha2}

    def state(self, commit: str, *, baseline: str | None = None,
              suffix: str = "") -> dict:
        return {"baselineCommit": baseline or self.base["baseline_commit"],
                "sourceState": commit + suffix}

    def test_source_state_contract_matrix(self) -> None:
        current = self.base["baseline_commit"]
        advanced = "1" * 40

        # 1. HEAD unchanged + clean.
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            self.state(current), self.base)["kind"], "CURRENT")
        # 2. HEAD unchanged + known dirty changes: the provenance is still
        # current; dirty checkout handling is asserted below.
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            self.state(current), self.base)["kind"], "CURRENT")
        # 3. A legal checkout fast-forward does not change a pinned runtime
        # source state while the canonical baseline remains unchanged.
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            self.state(current), self.base)["kind"], "CURRENT")
        # 4. Source changed but not rebuilt/applied.
        changed_cfg = {**self.base, "baseline_commit": advanced}
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            self.state(current), changed_cfg)["kind"], "STALE_DEPLOYED_RECIPE")
        # 5. Source changed and build/apply completed.
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            self.state(advanced, baseline=advanced), changed_cfg)["kind"], "CURRENT")
        # A manifest with a current sourceState but a different recorded
        # baseline is internally inconsistent provenance.
        inconsistent = self.state(advanced, baseline=current)
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            inconsistent, changed_cfg)["kind"], "SOURCE_CONTRACT_GAP")
        # 6. Runtime artifact drift is not source provenance drift.
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            self.state(advanced, baseline=advanced), changed_cfg)["kind"], "CURRENT")
        # 7. Generated artifact drift is not source provenance drift.
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            self.state(advanced, baseline=advanced), changed_cfg)["kind"], "CURRENT")
        # 8. An invalid/foreign source state is a contract gap, not an advance.
        malformed = self.state(advanced, baseline=advanced, suffix="+patch:bad")
        self.assertEqual(dsh_runtime._classify_ui_source_state(
            malformed, changed_cfg)["kind"], "SOURCE_CONTRACT_GAP")

    def test_checkout_matrix_distinguishes_clean_dirty_advance_rollback_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            git(root, "init", "-q")
            git(root, "config", "user.email", "aic@test.invalid")
            git(root, "config", "user.name", "AIC test")
            (root / "tracked.txt").write_text("one", encoding="utf-8")
            git(root, "add", "tracked.txt")
            git(root, "commit", "-qm", "one")
            first = git(root, "rev-parse", "HEAD")
            (root / "tracked.txt").write_text("two", encoding="utf-8")
            git(root, "commit", "-qam", "two")
            second = git(root, "rev-parse", "HEAD")
            cfg_first = {**self.base, "baseline_commit": first}

            git(root, "checkout", "-q", first)
            clean = dsh_runtime._classify_ui_source_checkout(root, cfg_first)
            self.assertEqual(clean["kind"], "CURRENT")
            self.assertEqual(clean["dirtyState"], "CLEAN")

            (root / "known-change.txt").write_text("kept", encoding="utf-8")
            dirty = dsh_runtime._classify_ui_source_checkout(root, cfg_first)
            self.assertEqual(dirty["kind"], "CURRENT")
            self.assertEqual(dirty["dirtyState"], "DIRTY_KNOWN")

            git(root, "checkout", "-q", second)
            advanced = dsh_runtime._classify_ui_source_checkout(root, cfg_first)
            self.assertEqual(advanced["kind"], "SOURCE_ADVANCED")
            self.assertEqual(advanced["dirtyState"], "DIRTY_KNOWN")
            self.assertFalse(advanced["reconciliationRequired"])

            git(root, "checkout", "-q", first)
            git(root, "checkout", "-qb", "divergent")
            (root / "tracked.txt").write_text("divergent", encoding="utf-8")
            git(root, "commit", "-qam", "divergent")
            cfg_second = {**self.base, "baseline_commit": second}
            divergent = dsh_runtime._classify_ui_source_checkout(root, cfg_second)
            self.assertEqual(divergent["kind"], "SOURCE_DIVERGED")

            git(root, "checkout", "-q", first)
            rollback = dsh_runtime._classify_ui_source_checkout(
                root, cfg_second)
            self.assertEqual(rollback["kind"], "SOURCE_ROLLBACK")

    def _make_inspect_fixture(self, root: Path, source_state: str,
                              manifest_baseline: str | None = None) -> tuple[Path, dict]:
        contract = aic.adapter_contract()
        cfg = contract["runtime_composition"]
        profile = root / "profiles" / "web"
        base_root = profile / f"base-dsh-{cfg['base']['version']}"
        dsh_root = base_root / "node_modules" / "@deepseek-ai" / "dsh"
        dsh_root.mkdir(parents=True)
        (dsh_root / "package.json").write_text(json.dumps({
            "name": cfg["base"]["package"], "version": cfg["base"]["version"]
        }), encoding="utf-8")
        (dsh_root / "lib").mkdir()
        (dsh_root / "lib" / "bin.js").write_text("base", encoding="utf-8")
        node = root / cfg["node"]["relative_to_dsh_home"] / "node.exe"
        node.parent.mkdir(parents=True)
        node.write_bytes(b"node-fixture")

        client_root = dsh_root / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation"
        client_root.mkdir(parents=True)
        (client_root / "package.json").write_text(json.dumps({"name": cfg["ui"]["client_package"]}), encoding="utf-8")
        (client_root / "lib").mkdir()
        client = client_root / "lib" / "client.js"
        client.write_text("client", encoding="utf-8")
        frontend_root = dsh_root / "node_modules" / "@deepseek-ai" / "dsh-web-frontend"
        frontend_root.mkdir(parents=True)
        (frontend_root / "package.json").write_text(json.dumps({"name": cfg["ui"]["web_package"]}), encoding="utf-8")
        web_dist = frontend_root / "dist"
        web_dist.mkdir()
        (web_dist / "index.html").write_text("web", encoding="utf-8")

        patch_path = profile / cfg["profile"]["patch_file"]
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_text, managed_hash = dsh_runtime.render_patch(None, cfg)
        patch_path.write_text(patch_text, encoding="utf-8")
        launcher = profile / cfg["profile"]["launcher_file"]
        launcher.write_text(dsh_runtime._powershell_launcher(cfg), encoding="utf-8")

        overlays = []
        for order, plugin in enumerate(cfg["managed_rows"]["plugins"], start=1):
            source = REPO / plugin["source_relative"] / plugin["entry_relative"]
            destination = profile / "plugins" / plugin["plugin_directory"] / plugin["entry_relative"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            source_hash = dsh_runtime.sha256_file(source)
            row = {
                "id": plugin["id"], "package": plugin["package"],
                "version": plugin["version"], "loadOrder": order,
                "sourceRelative": str(source.relative_to(REPO)).replace("\\", "/"),
                "sourceSha256": source_hash,
                "deploymentRelative": str(destination.relative_to(root)).replace("\\", "/"),
                "deploymentSha256": source_hash,
            }
            if plugin["id"] == "compaction-basic-convergence":
                marker = destination.parent / ".dsh-convergence.json"
                marker.write_text("marker", encoding="utf-8")
                row["markerSha256"] = dsh_runtime.sha256_file(marker)
            overlays.append(row)

        old_baseline = manifest_baseline or cfg["ui"]["baseline_commit"]
        payload = {
            "schemaVersion": 1,
            "compositionId": cfg["id"],
            "node": {"version": cfg["node"]["version"],
                     "relativePath": cfg["node"]["relative_to_dsh_home"],
                     "sha256": dsh_runtime.sha256_file(node)},
            "base": {"package": cfg["base"]["package"], "version": cfg["base"]["version"],
                     "entryRelative": str((Path("profiles/web") / base_root.name /
                                            cfg["base"]["entry_relative_to_distribution"])).replace("\\", "/"),
                     "entrySha256": dsh_runtime.sha256_file(dsh_root / "lib" / "bin.js")},
            "ui": {"repository": cfg["ui"]["repository"],
                   "baselineCommit": old_baseline, "sourceState": source_state,
                   "fixCommit": cfg["ui"]["fix_commit"],
                   "patchFile": cfg["ui"]["patch_file"], "patchSha256": cfg["ui"]["patch_sha256"],
                   "clientBundleRelative": str(client.relative_to(root)).replace("\\", "/"),
                   "clientBundleSha256": dsh_runtime.sha256_file(client),
                   "webDistRelative": str(web_dist.relative_to(root)).replace("\\", "/"),
                   "webDistSha256": dsh_runtime.sha256_tree(web_dist)},
            "overlays": overlays,
            "cordisPatch": {"relativePath": str(patch_path.relative_to(root)).replace("\\", "/"),
                            "managedBlockSha256": managed_hash},
            "launcher": {"relativePath": str(launcher.relative_to(root)).replace("\\", "/"),
                         "sha256": dsh_runtime.sha256_file(launcher)},
            "archiveAnchor": {"sessionId": cfg["archive_anchor"]["session_id"],
                              "status": cfg["archive_anchor"]["status"],
                              "operationalLabel": cfg["archive_anchor"]["operational_label"],
                              "artifactSha256": cfg["archive_anchor"]["artifact_sha256"]},
        }
        manifest, _ = dsh_runtime._stable_manifest(payload)
        (profile / cfg["profile"]["manifest_file"]).write_text(
            json.dumps(manifest), encoding="utf-8")
        return root, contract

    def _inspect_fixture(self, root: Path, contract: dict) -> dict:
        with mock.patch.object(dsh_runtime, "_run", return_value="v22.19.0"), \
                mock.patch.dict(os.environ, {"DSH_HARNESS_ROOT": ""}, clear=False):
            return dsh_runtime.inspect(root, contract)

    def test_inspect_matrix_separates_stale_runtime_and_generated_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "current"
            root, contract = self._make_inspect_fixture(
                root, aic.adapter_contract()["runtime_composition"]["ui"]["baseline_commit"])
            current = self._inspect_fixture(root, contract)
            self.assertEqual(current["status"], "PASS")
            self.assertEqual(current["sourceState"]["kind"], "CURRENT")

            stale_root = Path(td) / "stale"
            old = "b" * 40
            stale_root, stale_contract = self._make_inspect_fixture(
                stale_root, old, manifest_baseline=old)
            stale = self._inspect_fixture(stale_root, stale_contract)
            self.assertEqual(stale["findings"][0]["category"], "STALE_DEPLOYED_RECIPE")
            self.assertEqual(stale["sourceState"]["kind"], "STALE_DEPLOYED_RECIPE")

            runtime_root = Path(td) / "runtime"
            runtime_root, runtime_contract = self._make_inspect_fixture(
                runtime_root, aic.adapter_contract()["runtime_composition"]["ui"]["baseline_commit"])
            runtime_client = runtime_root / "profiles" / "web" / "base-dsh-0.1.1-rc.2" / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib" / "client.js"
            runtime_client.write_text("tampered", encoding="utf-8")
            runtime = self._inspect_fixture(runtime_root, runtime_contract)
            self.assertTrue(any(f["category"] == "DEPLOYMENT_DRIFT" for f in runtime["findings"]))
            self.assertFalse(any(f["component"] == "ui.source-state" for f in runtime["findings"]))

            generated_root = Path(td) / "generated"
            generated_root, generated_contract = self._make_inspect_fixture(
                generated_root, aic.adapter_contract()["runtime_composition"]["ui"]["baseline_commit"])
            generated_patch = generated_root / "profiles" / "web" / "cordis.patch.yml"
            generated_patch.write_text(
                generated_patch.read_text(encoding="utf-8").replace(
                    "token-meter-pressure-guard", "tampered-generated-block", 1),
                encoding="utf-8")
            generated = self._inspect_fixture(generated_root, generated_contract)
            self.assertTrue(any(f["category"] == "GENERATED_DRIFT" for f in generated["findings"]))
            self.assertFalse(any(f["component"] == "ui.source-state" for f in generated["findings"]))


if __name__ == "__main__":
    unittest.main()
