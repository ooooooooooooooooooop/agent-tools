"""Focused stdlib tests for the CLI adapter framework.

Covers: CliResult/CliBackend/CliRegistry contract, GenericCliBackend config-driven
adaptation (command building, model aliases, JSON/plain parsing), built-in adapter
registration, and route_agent_task custom-CLI early dispatch. Uses only unittest /
tempfile / unittest.mock; no real CLI or broker DB is touched.
"""
from __future__ import annotations

import json
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli_backend_base  # noqa: E402
from cli_backend_base import CliBackend, CliRegistry, CliResult  # noqa: E402
from cli_backends import (  # noqa: E402
    backends_from_config,
    register_builtin_backends,
)
from cli_backends.generic import GenericCliBackend  # noqa: E402
from cli_backends import plugin_loader  # noqa: E402


def _temp_broker_paths(broker_mod, tmpdir):
    """Patches the broker's module-level BROKER_DIR/DB/LOG/CONFIG paths onto a throwaway
    directory so init_db/routing never touches the real ~/.agent-broker (which may be
    readonly under the sandbox). Returns a contextlib.ExitStack already entered."""
    import contextlib

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(broker_mod, "BROKER_DIR", Path(tmpdir)))
    stack.enter_context(mock.patch.object(broker_mod, "DB_PATH", Path(tmpdir) / "state.sqlite"))
    stack.enter_context(mock.patch.object(broker_mod, "LOG_PATH", Path(tmpdir) / "agent-broker.log"))
    stack.enter_context(mock.patch.object(broker_mod, "CONFIG_PATH", Path(tmpdir) / "config.json"))
    return stack


class RegistryTests(unittest.TestCase):
    def test_register_get_by_name_and_alias(self):
        reg = CliRegistry()

        class A(CliBackend):
            name = "a"
            aliases = ["a1", "a2"]

        reg.register(A())
        self.assertIs(reg.get("a"), reg.get("A"))  # case-insensitive
        self.assertIs(reg.get("a1"), reg.get("a"))
        self.assertIs(reg.get("a2"), reg.get("a"))
        self.assertIn("a", reg.names())

    def test_unknown_returns_none(self):
        reg = CliRegistry()
        self.assertIsNone(reg.get("nope"))
        self.assertNotIn("nope", reg)

    def test_builtin_registration(self):
        reg = CliRegistry()
        register_builtin_backends(reg)
        for name in ("codex_cli", "claude_code", "antigravity_cli", "gemini_cli"):
            self.assertIsNotNone(reg.get(name))
        # Idempotent
        register_builtin_backends(reg)
        self.assertEqual(len(reg.names()), 4)


class SimpleBackend(CliBackend):
    @property
    def name(self) -> str:
        return "simple"

    def discover(self) -> str | None:
        return "C:/bin/simple"

    def parse_output(self, stdout, stderr, exit_code):
        return CliResult(
            response=f"parsed[{stdout}]",
            status="completed" if exit_code == 0 else "error",
            exit_code=exit_code,
            backend=self.name,
        )


class CliResultTests(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        r = CliResult(
            response="hi",
            status="completed",
            requested_model="m",
            actual_model="m",
            model_attested=True,
            exit_code=0,
            backend="b",
        )
        d = r.to_dict()
        self.assertEqual(d["response"], "hi")
        self.assertEqual(d["backend"], "b")
        self.assertIn("metadata", d)

    def test_execute_template_with_runner(self):
        backend = SimpleBackend()
        calls = []

        def fake_runner(command, cwd, timeout):
            calls.append((command, cwd, timeout))
            return 0, "world", ""

        result = backend.execute(
            "hello",
            runner=fake_runner,
            project_root="/tmp",
            timeout=9,
        )
        self.assertEqual(result.response, "parsed[world]")
        self.assertEqual(result.status, "completed")
        self.assertEqual(calls[0][2], 9)

    def test_execute_cli_not_found(self):
        backend = SimpleBackend()
        # Default discover() returns a path; temporarily override to None.
        with mock.patch.object(backend, "discover", return_value=None):
            result = backend.execute("hello", runner=lambda c, cwd, timeout: (0, "", ""))
        self.assertEqual(result.status, "cli_not_found")


class SubstituteTemplateTests(unittest.TestCase):
    def test_placeholders(self):
        out = cli_backend_base.substitute_template(
            ["{model}", "run", "{effort}", "{prompt}"],
            prompt="P",
            model="M",
            effort="E",
            timeout=30,
            project_root="/proj",
        )
        self.assertEqual(out, ["M", "run", "E", "P"])

    def test_unknown_placeholder_left_verbatim(self):
        out = cli_backend_base.substitute_template(
            ["{nope}", "{prompt}"], prompt="P", model=None, effort=None, timeout=30, project_root=None
        )
        self.assertEqual(out, ["{nope}", "P"])


class GenericBackendTests(unittest.TestCase):
    def _backend(self, **overrides):
        spec = {
            "command": "mytool",
            "args_template": ["run"],
            "prompt_arg": "{model} {prompt}",
            "default_model": "defaultm",
            "model_aliases": {"m1": "m1-full"},
            "description": "test tool",
            "output_format": "plain_text",
            "timeout": 30,
        }
        spec.update(overrides)
        return GenericCliBackend("mytool", spec)

    def test_discover_uses_which(self):
        with mock.patch("shutil.which", return_value="C:/bin/mytool.exe"):
            self.assertEqual(self._backend().discover(), "C:/bin/mytool.exe")

    def test_discover_explicit_path(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tf:
            tf.write(b"pass")
        try:
            b = self._backend(discovery=tf.name)
            self.assertEqual(b.discover(), tf.name)
        finally:
            import os
            os.unlink(tf.name)

    def test_build_command_resolves_alias_and_appends_prompt(self):
        b = self._backend()
        cmd = b.build_command("C:/bin/mytool.exe", "hello", model="m1", timeout=30)
        # [exe] + args_template("run") + prompt_arg("{model} {prompt}")
        self.assertEqual(cmd, ["C:/bin/mytool.exe", "run", "m1-full hello"])

    def test_execute_via_runner(self):
        b = self._backend()
        captured = {}

        def runner(command, cwd, timeout):
            captured["cmd"] = command
            return 0, "the answer", ""

        with mock.patch.object(b, "discover", return_value="C:/bin/mytool.exe"):
            result = b.execute("q", model="m1", runner=runner, project_root="/proj")
        self.assertEqual(result.response, "the answer")
        self.assertEqual(result.status, "completed")
        self.assertEqual(captured["cmd"][0], "C:/bin/mytool.exe")

    def test_execute_json_path(self):
        b = self._backend(output_format="json", json_path="message.text")
        with mock.patch.object(b, "discover", return_value="C:/bin/mytool.exe"):
            result = b.execute("q", runner=runner_ok('{"message":{"text":"extracted"}}'))
        self.assertEqual(result.response, "extracted")

    def test_execute_json_invalid(self):
        b = self._backend(output_format="json")
        with mock.patch.object(b, "discover", return_value="C:/bin/mytool.exe"):
            result = b.execute("q", runner=runner_ok("not json"))
        self.assertIn("no JSON", result.response)

    def test_execute_cli_not_found(self):
        b = self._backend()
        with mock.patch.object(b, "discover", return_value=None):
            result = b.execute("q")
        self.assertEqual(result.status, "cli_not_found")


def runner_ok(stdout: str):
    def _runner(command, cwd, timeout):
        return 0, stdout, ""
    return _runner


class BackendsFromConfigTests(unittest.TestCase):
    def test_parses_config_block(self):
        cfg = {
            "cli_backends": {
                "ollama": {
                    "command": "ollama",
                    "args_template": ["run"],
                    "prompt_arg": "{model} {prompt}",
                    "default_model": "llama3",
                },
                "bogus": "not-a-dict",
            }
        }
        backends = backends_from_config(cfg)
        names = [b.name for b in backends]
        self.assertIn("ollama", names)
        self.assertNotIn("bogus", names)

    def test_empty_and_non_dict(self):
        self.assertEqual(backends_from_config({}), [])
        self.assertEqual(backends_from_config({"cli_backends": "x"}), [])


class BuiltinAdapterTests(unittest.TestCase):
    def test_codex_adapter_delegates(self):
        from cli_backends.codex_backend import CodexCliBackend

        b = CodexCliBackend()
        fake = _fake_codex_result()

        # Patch the *source* function in agent_broker_mcp since the adapter imports
        # it via `from agent_broker_mcp import consult_codex` inside execute().
        import agent_broker_mcp as broker_mod

        with mock.patch.object(broker_mod, "consult_codex", return_value=fake) as m:
            with mock.patch.object(b, "discover", return_value="C:/bin/codex"):
                res = b.execute("prompt", model="gpt-5.5", effort="max")
        self.assertEqual(res.status, "completed")
        self.assertTrue(res.model_attested)
        self.assertEqual(res.response, "codex answer")
        m.assert_called_once()

    def test_claude_adapter_delegates(self):
        from cli_backends.claude_backend import ClaudeCliBackend

        class R:
            response = "claude answer"
            requested_model = "fable"
            actual_model = "claude-fable-5"
            model_attested = True
            initial_model = "fable"
            attempted_models = ()
            fallback_reason = None

        b = ClaudeCliBackend()
        import agent_broker_mcp as broker_mod

        with mock.patch.object(broker_mod, "consult_claude", return_value=R()) as m:
            with mock.patch.object(b, "discover", return_value="C:/bin/claude"):
                res = b.execute("prompt")
        self.assertEqual(res.response, "claude answer")
        self.assertTrue(res.model_attested)
        m.assert_called_once()

    def test_antigravity_adapter(self):
        from cli_backends.antigravity_backend import AntigravityCliBackend

        b = AntigravityCliBackend()
        import agent_broker_mcp as broker_mod

        with mock.patch.object(
            broker_mod,
            "consult_antigravity_cli",
            return_value="Antigravity CLI structured-output validation failed: x",
        ):
            with mock.patch.object(b, "discover", return_value="C:/bin/agy"):
                self.assertEqual(b.execute("p").status, "error")

    def test_gemini_adapter(self):
        from cli_backends.gemini_backend import GeminiCliBackend

        b = GeminiCliBackend()
        import agent_broker_mcp as broker_mod

        with mock.patch.object(broker_mod, "consult_gemini", return_value="gemini reply"):
            with mock.patch.object(b, "discover", return_value="C:/bin/gemini"):
                self.assertEqual(b.execute("p").status, "completed")


def _fake_codex_result():
    class R:
        response = "codex answer"
        requested_model = "gpt-5.5"
        actual_model = "gpt-5.5"
        requested_effort = "max"
        actual_effort = "max"
        model_attested = True
    return R()


class RouteCustomCliDispatchTests(unittest.TestCase):
    """route_agent_task must route an explicit custom target_agent to its backend."""

    def _fresh_registry_with_ollama(self):
        import agent_broker_mcp as broker_mod

        reg = CliRegistry()
        reg.register(
            GenericCliBackend(
                "ollama",
                {
                    "command": "ollama",
                    "args_template": ["run"],
                    "prompt_arg": "{model} {prompt}",
                    "default_model": "llama3",
                },
            )
        )

        class RegistryShell:
            @staticmethod
            def get(name):
                return reg.get(name)

            @staticmethod
            def all():
                return reg.all()

        broker_mod.cli_registry = lambda: RegistryShell()
        # Reset the module-level registry cache so the next cli_registry() call
        # uses our RegistryShell.
        broker_mod._cli_registry._registry = None
        return broker_mod

    def test_custom_target_routes_to_backend(self):
        import agent_broker_mcp as broker_mod
        m = self._fresh_registry_with_ollama()

        # Patch the dispatch helper so no real subprocess runs; assert it is invoked
        # for the ollama backend.
        with mock.patch.object(
            broker_mod,
            "_dispatch_custom_cli_backend",
            return_value={"status": "completed", "response": "from ollama"},
        ) as dm:
            result = broker_mod.route_agent_task(
                {"target_agent": "ollama", "prompt": "say hi", "target_model": ""}
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["response"], "from ollama")
        self.assertEqual(result["route"], "cli_backend")
        self.assertEqual(dm.call_count, 1)
        # The backend argument must be the ollama adapter.
        self.assertEqual(dm.call_args[0][0].name, "ollama")

    def test_builtin_target_not_captured_as_custom(self):
        import tempfile
        import agent_broker_mcp as broker_mod
        self._fresh_registry_with_ollama()

        # Stub the heavy downstream claude dispatch so no real CLI/worker runs; use a
        # throwaway broker home so init_db (e.g. via token-economy guard) never touches the
        # real ~/.agent-broker.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            stack = _temp_broker_paths(broker_mod, td)
            stub_proj = broker_mod.ProjectInfo(name="proj", root_path=str(REPO_ROOT))
            try:
                with mock.patch.object(broker_mod, "_dispatch_custom_cli_backend") as dm:
                    with mock.patch.object(broker_mod, "resolve_project", return_value=stub_proj):
                        with mock.patch.object(
                            broker_mod, "queue_claude_request", return_value={"status": "queued"}
                        ):
                            with mock.patch.object(
                                broker_mod,
                                "write_app_handoff_file",
                                return_value={"status": "handoff"},
                            ):
                                with mock.patch.object(
                                    broker_mod, "consult", return_value={"status": "completed", "response": "x"}
                                ):
                                    with mock.patch.object(broker_mod, "log"):
                                        broker_mod.route_agent_task(
                                            {"target_agent": "claude", "prompt": "hello", "target_model": ""}
                                        )
            finally:
                stack.close()
        # Built-in claude must NOT have been routed through the custom dispatcher.
        dm.assert_not_called()


class DoctorCliBackendTests(unittest.TestCase):
    """doctor must report registered CLI backends (built-in + custom)."""

    def test_report_lists_builtins(self):
        import agent_broker_mcp as broker_mod
        reg = CliRegistry()
        register_builtin_backends(reg)
        with mock.patch.object(broker_mod, "cli_registry", return_value=reg):
            report = broker_mod._report_cli_backends()
        names = [b["name"] for b in report["backends"]]
        self.assertIn("codex_cli", names)
        self.assertIn("gemini_cli", names)
        self.assertEqual(report["custom_count"], 0)

    def test_report_lists_custom_and_flags_unavailable(self):
        import agent_broker_mcp as broker_mod
        reg = CliRegistry()
        register_builtin_backends(reg)
        reg.register(
            GenericCliBackend(
                "ollama",
                {"command": "ollama", "args_template": ["run"], "prompt_arg": "{prompt}"},
            )
        )
        with mock.patch.object(broker_mod, "cli_registry", return_value=reg):
            with mock.patch.object(reg.get("ollama"), "available", return_value=False):
                report = broker_mod._report_cli_backends()
        self.assertEqual(report["custom_count"], 1)
        self.assertEqual(report["custom_unavailable"], 1)
        self.assertIn("ollama", report["registered"])

    def test_doctor_includes_cli_backends_block(self):
        import tempfile
        import agent_broker_mcp as broker_mod
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            stack = _temp_broker_paths(broker_mod, td)
            reg = CliRegistry()
            register_builtin_backends(reg)
            try:
                with mock.patch.object(broker_mod, "cli_registry", return_value=reg):
                    report = broker_mod.broker_doctor()
            finally:
                stack.close()
        self.assertIn("cli_backends", report)
        text = broker_mod.render_doctor(report)
        self.assertIn("[cli backends: registered adapters", text)
        self.assertIn("codex_cli", text)


class AsyncCliRequestTests(unittest.TestCase):
    """queue_cli_request -> worker -> request_result / request_status lifecycle."""

    def setUp(self):
        import tempfile

        import agent_broker_mcp as broker_mod
        self.broker = broker_mod
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._path_stack = _temp_broker_paths(self.broker, self.tmp.name)

        # Fresh registry with a deterministic custom backend.
        reg = CliRegistry()
        reg.register(
            GenericCliBackend(
                "echo",
                {"command": "echo", "args_template": ["{prompt}"], "default_model": "m"},
            )
        )
        self.reg = reg
        self.patch_reg = mock.patch.object(self.broker, "cli_registry", return_value=reg)
        self.patch_reg.start()

    def tearDown(self):
        self.patch_reg.stop()
        self._path_stack.close()
        self.tmp.cleanup()

    def test_queue_then_worker_then_request_result(self):
        b = self.broker
        # Discover + run_process mocked for BOTH queue and worker so no real subprocess runs.
        with mock.patch.object(self.reg.get("echo"), "discover", return_value="C:/bin/echo"):
            with mock.patch.object(b, "run_process", return_value=(0, "async reply", "")):
                queued = b.queue_cli_request("echo", "hello", project="p", autorun=False)
                self.assertEqual(queued["status"], "queued")
                rid = queued["id"]
                res = b.run_cli_request_worker(rid)
        self.assertEqual(res["status"], "completed")
        rr = b.request_result(rid)
        self.assertEqual(rr["state"], "completed")
        self.assertEqual(rr["response"], "async reply")
        st = b.request_status(rid)
        self.assertEqual(st["kind"], "cli")
        self.assertTrue(st["answered"])

    def test_get_cli_requests_lists_async_rows(self):
        b = self.broker
        with mock.patch.object(self.reg.get("echo"), "discover", return_value="C:/bin/echo"):
            with mock.patch.object(b, "run_process", return_value=(0, "ok", "")):
                queued = b.queue_cli_request("echo", "job", project="projx", autorun=False)
        listing = b.get_cli_requests("projx")
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["requests"][0]["backend"], "echo")
        self.assertIn("echo", listing["backends"])

    def test_chain_budget_blocks_review_repair_nesting(self):
        # Same chain_key may be queued up to CHAIN_BUDGET_MAX times; the next
        # attempt is refused so a review->repair loop cannot nest forever.
        b = self.broker
        with mock.patch.object(self.reg.get("echo"), "discover", return_value="C:/bin/echo"):
            with mock.patch.object(b, "run_process", return_value=(0, "ok", "")):
                for i in range(b.CHAIN_BUDGET_MAX):
                    queued = b.queue_cli_request(
                        "echo", f"review round {i}", project="p",
                        autorun=False, chain_key="artifact-x",
                    )
                    self.assertEqual(queued["status"], "queued")
                with self.assertRaisesRegex(ValueError, "chain_budget_exceeded"):
                    b.queue_cli_request(
                        "echo", "review round 4", project="p",
                        autorun=False, chain_key="artifact-x",
                    )
        # A different chain key is unaffected.
        with mock.patch.object(self.reg.get("echo"), "discover", return_value="C:/bin/echo"):
            with mock.patch.object(b, "run_process", return_value=(0, "ok", "")):
                queued = b.queue_cli_request(
                    "echo", "other chain", project="p",
                    autorun=False, chain_key="artifact-y",
                )
        self.assertEqual(queued["status"], "queued")

    def test_worker_marks_cli_not_found(self):
        b = self.broker
        with mock.patch.object(self.reg.get("echo"), "discover", return_value=None):
            queued = b.queue_cli_request("echo", "x", project="p", autorun=False)
            res = b.run_cli_request_worker(queued["id"])
        self.assertEqual(res["status"], "error")
        self.assertIn("not found", b.request_result(queued["id"])["response"])


class PluginLoaderTests(unittest.TestCase):
    """Directory-scan plugin discovery for CLI backends."""

    def _write_plugin(self, tmpdir, name, content, meta=None):
        p = Path(tmpdir) / f"{name}.py"
        p.write_text(content, encoding="utf-8")
        if meta:
            (Path(tmpdir) / "plugin.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
        return p

    def test_discover_backend_instance(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            self._write_plugin(
                td,
                "mycli",
                textwrap.dedent("""\
                from cli_backends.generic import GenericCliBackend
                backend = GenericCliBackend("mycli", {"command":"echo","prompt_arg":"{prompt}","default_model":"m"})
                """),
            )
            backends, errors = plugin_loader.backends_from_directory(td)
        self.assertEqual(len(backends), 1)
        self.assertEqual(backends[0].name, "mycli")
        self.assertEqual(errors, [])

    def test_discover_make_backend(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            self._write_plugin(
                td,
                "factory",
                textwrap.dedent("""\
                from cli_backend_base import CliBackend
                class Fact(CliBackend):
                    @property
                    def name(self): return "factory"
                    def parse_output(self, stdout,stderr,exit_code):
                        from cli_backend_base import CliResult
                        return CliResult(response=stdout,status="completed" if exit_code==0 else "error",exit_code=exit_code,backend=self.name)
                def make_backend(): return Fact()
                """),
            )
            backends, errors = plugin_loader.backends_from_directory(td)
        self.assertEqual(len(backends), 1)
        self.assertEqual(backends[0].name, "factory")

    def test_rejects_empty_subclass(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            self._write_plugin(
                td,
                "empty",
                textwrap.dedent("""\
                from cli_backend_base import CliBackend
                class Empty(CliBackend):
                    @property
                    def name(self): return "empty"
                """),
            )
            backends, errors = plugin_loader.backends_from_directory(td)
        self.assertEqual(len(backends), 0)
        self.assertTrue(any("no `backend`/`make_backend`/CliBackend subclass" in e for e in errors))

    def test_sha256_strict_skips_mismatch(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            p = self._write_plugin(
                td,
                "pinned",
                textwrap.dedent("""\
                from cli_backends.generic import GenericCliBackend
                CLI_PLUGIN_META = {"name":"pinned","sha256":"DEADBEEF"}
                backend = GenericCliBackend("pinned", {"command":"echo","prompt_arg":"{prompt}"})
                """),
            )
            backends, errors = plugin_loader.backends_from_directory(td, strict=True)
            self.assertEqual(len(backends), 0)
            self.assertTrue(any("sha256 mismatch" in e for e in errors))
            # Strict=False loads anyway
            backends, errors = plugin_loader.backends_from_directory(td, strict=False)
            self.assertEqual(len(backends), 1)

    def test_plugin_json_checksum(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            self._write_plugin(
                td, "x", "from cli_backends.generic import GenericCliBackend\nbackend = GenericCliBackend('x', {'command':'echo','prompt_arg':'{prompt}'})\n",
                meta={"sha256": "notreal"},
            )
            # plugin.json sha256 should be checked
            backends, errors = plugin_loader.backends_from_directory(td, strict=True)
            self.assertEqual(len(backends), 0)

    def test_register_into_registry(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            self._write_plugin(
                td, "pcli",
                "from cli_backends.generic import GenericCliBackend\nbackend = GenericCliBackend('pcli', {'command':'echo','prompt_arg':'{prompt}'})\n",
            )
            reg = CliRegistry()
            register_builtin_backends(reg)
            notes = plugin_loader.register_backends_from_directory(reg, td)
        self.assertIn("pcli", reg.names())
        self.assertTrue(any("registered 'pcli'" in n for n in notes))

    def test_broken_import_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            (Path(td) / "broken.py").write_text("this is not valid python {{{", encoding="utf-8")
            backends, errors = plugin_loader.backends_from_directory(td)
        self.assertEqual(len(backends), 0)
        self.assertTrue(any("import failed" in e for e in errors))


class CapabilitiesBackendTypeTests(unittest.TestCase):
    """Declarative capabilities and backend_type for all adapters."""

    def test_base_backend_type_cli(self):
        """A plain CliBackend subclass defaults to cli type with chat capability."""
        class B(CliBackend):
            @property
            def name(self): return "test"
        self.assertEqual(B().backend_type, "cli")
        self.assertIn("chat", B().capabilities)

    def test_codex_capabilities(self):
        from cli_backends.codex_backend import CodexCliBackend
        b = CodexCliBackend()
        self.assertIn("coding", b.capabilities)
        self.assertIn("sandbox", b.capabilities)
        self.assertEqual(b.backend_type, "cli")

    def test_claude_capabilities(self):
        from cli_backends.claude_backend import ClaudeCliBackend
        b = ClaudeCliBackend()
        self.assertIn("streaming", b.capabilities)
        self.assertIn("tool_use", b.capabilities)
        self.assertEqual(b.backend_type, "cli")

    def test_generic_custom_capabilities_via_config(self):
        b = GenericCliBackend("mytool", {"capabilities": ["coding", "reasoning"]})
        self.assertIn("coding", b.capabilities)
        self.assertIn("reasoning", b.capabilities)

    def test_openai_compat_backend_type(self):
        b = GenericCliBackend("ollama", {"type": "openai_compat", "base_url": "http://local:11434", "default_model": "m"})
        self.assertEqual(b.backend_type, "http")
        self.assertIn("json", b.capabilities)
        self.assertIn("reasoning", b.capabilities)

    def test_http_execute_requires_base_url(self):
        b = GenericCliBackend("test", {"type": "http", "default_model": "m"})
        r = b.execute("hello", timeout=2)
        # Without a reachable base_url, it tries localhost:11434 and must return an
        # error status (connection refused, or an HTTP error if a server is up) — never a crash.
        self.assertEqual(r.status, "error")
        self.assertTrue(r.response.startswith("test HTTP"))

    def test_http_execute_no_model(self):
        b = GenericCliBackend("test", {"type": "http", "base_url": "http://local:9999"})
        r = b.execute("hello")
        self.assertEqual(r.status, "error")
        self.assertIn("no model was specified", r.response)

    def test_http_discover_returns_base_url(self):
        b = GenericCliBackend("test", {"type": "http", "base_url": "http://local:11434", "default_model": "m"})
        self.assertEqual(b.discover(), "http://local:11434")

    def test_http_metadata_includes_base_url(self):
        b = GenericCliBackend("test", {"type": "http", "base_url": "http://local:11434", "default_model": "m"})
        md = b.metadata()
        self.assertEqual(md["backend_type"], "http")
        self.assertEqual(md["base_url"], "http://local:11434")
        self.assertIn("json", md["capabilities"])

    def test_list_cli_backends_shows_capabilities(self):
        """Simulate what list_cli_backends MCP tool produces."""
        from cli_backends.codex_backend import CodexCliBackend
        b = CodexCliBackend()
        md = b.metadata()
        self.assertIn("capabilities", md)
        self.assertIsInstance(md["capabilities"], list)
        self.assertIn("backend_type", md)
        self.assertEqual(md["backend_type"], "cli")


class TaskRoutingComparePipelineTests(unittest.TestCase):
    """Configurable task->backend routing, compare, and pipeline."""

    def test_infer_task_type_keywords(self):
        import agent_broker_mcp as m
        self.assertEqual(m.infer_task_type_from_prompt("fix this python bug"), "coding")
        self.assertEqual(m.infer_task_type_from_prompt("调研这篇论文并分析"), "research")
        self.assertEqual(m.infer_task_type_from_prompt("帮我润色这段中文文案"), "writing")
        self.assertEqual(m.infer_task_type_from_prompt("quick cheap summary"), "fast")
        self.assertEqual(m.infer_task_type_from_prompt("hello world"), "general")

    def test_infer_task_type_explicit_wins(self):
        import agent_broker_mcp as m
        # Requested non-auto task type overrides keyword inference.
        self.assertEqual(m.infer_task_type_from_prompt("hello", "review"), "review")

    def test_route_from_preferences_resolves_registered(self):
        import agent_broker_mcp as m
        reg = CliRegistry()
        reg.register(GenericCliBackend("myres", {"command": "echo", "default_model": "res-m"}))
        config = {"routing_preferences": {"research": ["myres"]}}
        out = m.route_from_task_preferences("do research on X", config, reg)
        self.assertIsNotNone(out)
        self.assertEqual(out[0], "myres")
        self.assertEqual(out[1], "res-m")

    def test_route_from_preferences_unregistered_returns_none(self):
        import agent_broker_mcp as m
        reg = CliRegistry()
        config = {"routing_preferences": {"coding": ["not-a-backend"], "writing": ["codex_cli"]}}
        # coding: preferred backend not registered -> falls through to None
        self.assertIsNone(m.route_from_task_preferences("fix bug", config, reg))

    def test_route_from_preferences_empty_config_none(self):
        import agent_broker_mcp as m
        reg = CliRegistry()
        self.assertIsNone(m.route_from_task_preferences("fix bug", {}, reg))

    def test_compare_executes_all_backends_with_runner(self):
        import agent_broker_mcp as m
        reg = CliRegistry()
        reg.register(GenericCliBackend("a", {"command": "echo", "prompt_arg": "{prompt}"}))
        reg.register(GenericCliBackend("b", {"command": "echo", "prompt_arg": "{prompt}"}))
        with mock.patch.object(m, "cli_registry", return_value=reg):
            with mock.patch.object(reg.get("a"), "available", return_value=True):
                with mock.patch.object(reg.get("b"), "available", return_value=True):
                    with mock.patch.object(
                        reg.get("a"), "execute",
                        return_value=CliResult(response="from A", status="completed", backend="a"),
                    ):
                        with mock.patch.object(
                            reg.get("b"), "execute",
                            return_value=CliResult(response="from B", status="completed", backend="b"),
                        ):
                            res = m.compare_cli_backends("prompt", backends=["a", "b"])
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["count"], 2)
        self.assertEqual([r["response"] for r in res["results"]], ["from A", "from B"])

    def test_pipeline_chains_output(self):
        import agent_broker_mcp as m
        reg = CliRegistry()

        class EchoBackend(CliBackend):
            def __init__(self, name):
                self._name = name
            @property
            def name(self): return self._name
            def available(self): return True
            def metadata(self): return {"default_model": None}
            def execute(self, prompt, model=None, effort=None, mode="read-only",
                        project_root=None, timeout=300, **kw):
                return CliResult(response=f"{self._name}:{prompt}", status="completed", backend=self._name)

        reg.register(EchoBackend("a"))
        reg.register(EchoBackend("b"))
        with mock.patch.object(m, "cli_registry", return_value=reg):
            res = m.run_cli_pipeline("start", steps=[{"backend": "a"}, {"backend": "b"}])
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["step_count"], 2)
        # step1 output feeds step2
        self.assertEqual(res["steps"][1]["response"], "b:a:start")

    def test_pipeline_unknown_backend_stops(self):
        import agent_broker_mcp as m
        reg = CliRegistry()
        with mock.patch.object(m, "cli_registry", return_value=reg):
            res = m.run_cli_pipeline("x", steps=[{"backend": "ghost"}])
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["steps"][0]["status"], "error")
        self.assertIn("unknown backend", res["steps"][0]["error"])

    def test_pipeline_empty_steps_error(self):
        import agent_broker_mcp as m
        res = m.run_cli_pipeline("x", steps=[])
        self.assertEqual(res["status"], "error")


class CompareParallelTests(unittest.TestCase):
    """compare_cli_backends runs backends concurrently (default) with order preserved."""

    class _Sleepy(CliBackend):
        def __init__(self, name, delay):
            self._name, self._delay = name, delay

        @property
        def name(self):
            return self._name

        def available(self):
            return True

        def metadata(self):
            return {"default_model": None}

        def execute(self, prompt, model=None, timeout=300, **kw):
            import time
            time.sleep(self._delay)
            return CliResult(response=f"{self._name} done", status="completed", backend=self._name)

    def _registry(self):
        reg = CliRegistry()
        reg.register(self._Sleepy("a", 0.3))
        reg.register(self._Sleepy("b", 0.05))
        reg.register(self._Sleepy("c", 0.0))
        return reg

    def test_parallel_wall_time_near_slowest(self):
        import time
        import agent_broker_mcp as m
        with mock.patch.object(m, "cli_registry", return_value=self._registry()):
            t0 = time.monotonic()
            res = m.compare_cli_backends("x", backends=["a", "b", "c"], parallel=True)
            wall = time.monotonic() - t0
        # slowest is 0.3s; parallel wall should be well under sum (0.35s) and near 0.3s.
        self.assertLess(wall, 0.35)
        self.assertGreaterEqual(wall, 0.25)
        self.assertEqual(res["status"], "completed")
        self.assertEqual([r["backend"] for r in res["results"]], ["a", "b", "c"])

    def test_sequential_is_slower(self):
        import time
        import agent_broker_mcp as m
        with mock.patch.object(m, "cli_registry", return_value=self._registry()):
            t0 = time.monotonic()
            res = m.compare_cli_backends("x", backends=["a", "b", "c"], parallel=False)
            wall = time.monotonic() - t0
        self.assertGreaterEqual(wall, 0.30)
        self.assertEqual(res["status"], "completed")
        self.assertEqual([r["backend"] for r in res["results"]], ["a", "b", "c"])

    def test_order_preserved_in_parallel(self):
        import agent_broker_mcp as m
        with mock.patch.object(m, "cli_registry", return_value=self._registry()):
            res = m.compare_cli_backends("x", backends=["c", "a", "b"], parallel=True)
        self.assertEqual([r["backend"] for r in res["results"]], ["c", "a", "b"])

    def test_single_backend_runs_even_in_parallel_mode(self):
        import agent_broker_mcp as m
        with mock.patch.object(m, "cli_registry", return_value=self._registry()):
            res = m.compare_cli_backends("x", backends=["c"], parallel=True)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["backend"], "c")


if __name__ == "__main__":
    unittest.main()
