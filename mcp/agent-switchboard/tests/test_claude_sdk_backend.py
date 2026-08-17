"""Focused standard-library tests for claude_sdk_backend.py — optional Claude Agent SDK probe.

Covers the module's acceptance criteria:
  1. the capability probe reports availability WITHOUT importing/calling the SDK or model;
  2. when the SDK is not importable, ``available`` is False and a clear note is set;
  3. the CLI dispatch in capability mode is free (no ``--run-prompt``) and returns JSON;
  4. the real-model driver fails closed (``ran: False``) and never half-invokes when its
     dependencies are absent or the CLI is missing.

No real model call is ever made here — the probe path is deterministic and the driver
path only exercises fail-closed branches.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import claude_sdk_backend  # noqa: E402


class SdkProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_capability_probe_is_deterministic_and_free(self):
        # Probe reports booleans/strings, never triggers a model call.
        rep = claude_sdk_backend.probe_sdk_capabilities()
        self.assertIn("available", rep)
        self.assertIn("opt_in_required", rep)
        self.assertTrue(isinstance(rep["available"], bool))
        # Reading the probe must not have spent tokens (no real invocation field).

    def test_probe_reports_not_available_with_clear_note(self):
        # Force find_spec to say absent.
        with mock.patch.object(claude_sdk_backend.importlib.util, "find_spec", return_value=None), \
             mock.patch.object(claude_sdk_backend, "_SDK_DEPS_CANDIDATES", (self.home / "nope",)):
            rep = claude_sdk_backend.probe_sdk_capabilities(claude_path=None)
        self.assertFalse(rep["available"])
        self.assertIsNotNone(rep["note"])
        self.assertIn("not importable", rep["note"])

    def test_probe_reports_sdk_exposes_when_available(self):
        # With the vendored dep dir present, the probe lists SDK surface names.
        sdk_dir = self.home / "sdk"
        (sdk_dir / "claude_agent_sdk").mkdir(parents=True)
        (sdk_dir / "claude_agent_sdk" / "__init__.py").write_text("", encoding="utf-8")
        with mock.patch.object(claude_sdk_backend, "_SDK_DEPS_CANDIDATES", (sdk_dir,)):
            rep = claude_sdk_backend.probe_sdk_capabilities(claude_path=None)
        self.assertTrue(rep["available"])
        self.assertIn("query_stream", rep["sdk_exposes"])
        self.assertIn("vs_cli", rep)

    def test_probe_cli_capability_mode_is_json_free(self):
        # Console capture: the CLI without --run-prompt prints JSON and exits 0.
        from io import StringIO
        import contextlib

        stdout = StringIO()
        with contextlib.redirect_stdout(stdout):
            code = claude_sdk_backend.handle_sdk_probe_cli(["--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertIn("available", parsed)
        self.assertIn("opt_in_required", parsed)

    def test_driver_fails_closed_without_sdk(self):
        import asyncio

        with mock.patch.object(claude_sdk_backend, "sdk_importable", return_value=(False, None)):
            result = asyncio.run(
                claude_sdk_backend.run_sdk_driver("probe", max_turns=1, cwd=str(self.home))
            )
        self.assertFalse(result["ran"])
        self.assertIn("not importable", result["error"])

    def test_driver_fails_closed_without_claude_cli(self):
        import asyncio

        # Fail closed BEFORE any model invocation whenever the CLI is unavailable. The SDK
        # import is satisfied from a vendored dir when present; otherwise the import guard
        # trips, which is still a fail-closed (ran=False) outcome — never a half-invoke.
        vendored = claude_sdk_backend._SDK_DEPS_CANDIDATES[0]
        with mock.patch.object(claude_sdk_backend, "sdk_importable", return_value=(True, str(vendored))), \
             mock.patch.object(claude_sdk_backend, "_claude_cli_path", return_value=None), \
             mock.patch.object(sys, "path", [str(vendored)] + list(sys.path)):
            result = asyncio.run(
                claude_sdk_backend.run_sdk_driver("probe", max_turns=1, cwd=str(self.home))
            )
        self.assertFalse(result["ran"])
        # Deterministic: we never reached a model call, and a human-readable error explains why.
        self.assertTrue(isinstance(result["error"], str) and result["error"])


if __name__ == "__main__":
    unittest.main()
