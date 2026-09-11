#!/usr/bin/env python3
"""Regression: the deployed DSH preflight gate must be self-contained and
location-independent.

Incident 2026-09-05: a verbatim copy of scripts/aic/dsh_compatibility.py was
hand-deployed to ~/.dsh/profiles/web/dsh-preflight.py. Its module-level
ROOT = Path(__file__).parents[2] resolved to ~/.dsh, where
registry/harnesses/dsh.yaml does not exist, so the gate crashed (exit 1) on
every launch and the fail-closed launcher refused to start DSH at all — the
web UI stayed permanently disconnected. These tests pin the fixed contract:

  1. deploy_gate() generates an artifact that PASSES at whatever location it
     was deployed to (no repo dependency at runtime).
  2. A raw verbatim copy (the incident mistake) fails LOUDLY, never silently.
  3. Both launcher templates stay fail-closed and decide on exit code only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "aic" / "dsh_compatibility.py"
LAUNCHER_TEMPLATE = ROOT / "dsh-config" / "profiles" / "web" / "dsh-launch-web.ps1"
RUNTIME_TEMPLATE = ROOT / "scripts" / "aic" / "dsh_runtime.py"


def _load_engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dsh_compatibility_deploy_test", ENGINE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # required before exec: dataclass resolution looks modules up by name
    spec.loader.exec_module(module)
    return module


class PreflightDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh-preflight-deploy-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _fake_composition(self, profile: Path) -> None:
        """Minimal artifacts so the existence-level identity gates can pass."""
        nm = profile / "node_modules" / "@deepseek-ai"
        (nm / "dsh-client-ui-conversation" / "lib").mkdir(parents=True, exist_ok=True)
        (nm / "dsh-client-ui-conversation" / "lib" / "client.js").write_text(
            'const inject=["slots","layout"];', encoding="utf-8")
        (nm / "dsh-web-frontend" / "dist").mkdir(parents=True, exist_ok=True)

    def test_generated_gate_passes_at_deployed_location(self):
        engine = _load_engine()
        profile = self.tmp / "home with spaces" / "profiles" / "web"
        self._fake_composition(profile)
        report = engine.deploy_gate(profile)
        self.assertTrue(report["artifact_functional"],
                        f"deployed gate did not produce a valid verdict: {report}")
        self.assertTrue(report["preflight_passed"],
                        f"deployed gate rejected its composition: {report['preflight']}")

        # Run the artifact exactly as the launcher does, from its deployed path.
        proc = subprocess.run(
            [sys.executable, str(profile / "dsh-preflight.py"),
             "--profile", str(profile), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(proc.returncode, 0,
                         f"deployed gate failed at deployed location:\n{proc.stderr}")
        verdict = json.loads(proc.stdout)
        self.assertTrue(verdict["passed"], verdict.get("errors"))

        # Self-contained: the artifact must not reference the authoring repo.
        deployed_text = (profile / "dsh-preflight.py").read_text(encoding="utf-8")
        self.assertNotIn(str(ROOT), deployed_text)
        self.assertIn("EMBEDDED_SSOT", deployed_text)

    def test_raw_engine_copy_fails_closed_at_deployed_location(self):
        """A hand-copied engine (the incident mistake) must fail LOUDLY."""
        profile = self.tmp / "profiles" / "web"
        profile.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ENGINE, profile / "dsh-preflight.py")

        # Hermetic: point DSH_CONTRACT_ROOT at an SSOT without a compatibility
        # block, so the outcome never depends on machine state (~/.dsh layout).
        bad_root = self.tmp / "contract-root"
        (bad_root / "registry" / "harnesses").mkdir(parents=True, exist_ok=True)
        (bad_root / "registry" / "harnesses" / "dsh.yaml").write_text(
            "runtime_composition: {}\n", encoding="utf-8")
        env = dict(os.environ, DSH_CONTRACT_ROOT=str(bad_root))

        proc = subprocess.run(
            [sys.executable, str(profile / "dsh-preflight.py"),
             "--profile", str(profile), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, env=env)
        self.assertNotEqual(proc.returncode, 0,
                            "raw engine copy must not silently pass at a deployed location")
        combined = proc.stderr + proc.stdout
        self.assertIn("compatibility", combined)
        self.assertIn("deploy", combined)

    def test_launcher_templates_stay_fail_closed(self):
        for name, text in (
            ("dsh-launch-web.ps1", LAUNCHER_TEMPLATE.read_text(encoding="utf-8")),
            ("dsh_runtime.py embedded launcher", RUNTIME_TEMPLATE.read_text(encoding="utf-8")),
        ):
            with self.subTest(template=name):
                self.assertIn("PREFLIGHT_GATE_MISSING", text,
                              "launcher must refuse to start without the generated gate")
                self.assertIn("--json", text,
                              "launcher must invoke the gate in machine-readable mode")
                self.assertNotIn("Desktop\\skills", text,
                                 "launcher must not fall back to a hardcoded repo path")
                self.assertIn("dsh-preflight.py", text)


if __name__ == "__main__":
    unittest.main()
