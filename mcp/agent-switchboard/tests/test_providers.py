"""Focused tests for the providers (execution-layer) management.

Covers: ProviderConfig parsing, auth_available probe (file + env), registration into
registry, list_providers output, model-enforcement guard, and route-time dispatch.
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

import providers  # noqa: E402
from providers import ProviderConfig, auth_available  # noqa: E402
from cli_backend_base import CliRegistry, CliBackend, CliResult  # noqa: E402


class ProviderConfigParsingTests(unittest.TestCase):
    def test_official_cli(self):
        cfg = {
            "providers": {
                "claude": {
                    "type": "official_cli",
                    "cli": "claude",
                    "models": ["opus", "sonnet"],
                }
            }
        }
        ps = providers.providers_from_config(cfg)
        self.assertEqual(len(ps), 1)
        p = ps[0]
        self.assertEqual(p.name, "claude")
        self.assertEqual(p.provider_type, "official_cli")
        self.assertEqual(p.cli, "claude")
        self.assertEqual(p.models, ["opus", "sonnet"])

    def test_openai_compat(self):
        cfg = {
            "providers": {
                "deepseek": {
                    "type": "openai_compat",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "env:DEEPSEEK_KEY",
                    "models": ["deepseek-chat"],
                }
            }
        }
        ps = providers.providers_from_config(cfg)
        self.assertEqual(len(ps), 1)
        p = ps[0]
        self.assertEqual(p.name, "deepseek")
        self.assertEqual(p.provider_type, "openai_compat")
        self.assertEqual(p.base_url, "https://api.deepseek.com")
        self.assertEqual(p.models, ["deepseek-chat"])

    def test_env_key_resolved(self):
        cfg = {"providers": {"test": {"type": "openai_compat", "api_key": "env:TEST_KEY", "base_url": "http://localhost:9999"}}}
        os.environ["TEST_KEY"] = "sk-test"
        try:
            ps = providers.providers_from_config(cfg)
            self.assertEqual(ps[0].api_key, "sk-test")
        finally:
            os.environ.pop("TEST_KEY", None)

    def test_env_key_resolves_to_none_when_unset(self):
        cfg = {"providers": {"test": {"type": "openai_compat", "api_key": "env:MISSING_KEY", "models": []}}}
        ps = providers.providers_from_config(cfg)
        self.assertIsNone(ps[0].api_key)

    def test_skips_malformed(self):
        cfg = {"providers": {"good": {"type": "openai_compat", "base_url": "http://localhost:9999"}, "bad": "not-a-dict"}}
        ps = providers.providers_from_config(cfg)
        self.assertEqual(len(ps), 1)


class AuthAvailableTests(unittest.TestCase):
    """auth_available uses providers._home(); redirect to a temp dir for determinism."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._home_patch = mock.patch.object(providers, "_home", return_value=Path(self._tmp.name))
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._tmp.cleanup()

    def test_unknown_cli_assumes_available(self):
        self.assertTrue(auth_available("unknown-cli"))

    def test_empty_or_none_returns_false(self):
        self.assertFalse(auth_available(""))
        self.assertFalse(auth_available(None))

    def test_env_probe_true(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        try:
            self.assertTrue(auth_available("claude"))
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_env_probe_false_when_unset(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertFalse(auth_available("claude"))

    def test_claude_cred_file(self):
        cred = Path(self._tmp.name) / ".claude" / ".credentials.json"
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text("{}", encoding="utf-8")
        self.assertTrue(auth_available("claude"))

    def test_codex_auth_file(self):
        auth = Path(self._tmp.name) / ".codex" / "auth.json"
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text("{}", encoding="utf-8")
        self.assertTrue(auth_available("codex"))


class RegistryRegistrationTests(unittest.TestCase):
    """Test _register_providers against a fresh registry (isolated from global state)."""

    def _fresh_with_providers(self, cfg: dict) -> CliRegistry:
        import agent_broker_mcp as m

        reg = CliRegistry()
        # Register built-ins so official_cli can alias them.
        from cli_backends import register_builtin_backends

        register_builtin_backends(reg)
        with mock.patch.object(m, "load_config", return_value=cfg):
            m._register_providers(reg)
        return reg

    def test_providers_registered(self):
        cfg = {"providers": {"test-provider": {"type": "openai_compat", "base_url": "http://localhost:9999", "models": ["m1"]}}}
        reg = self._fresh_with_providers(cfg)
        self.assertIsNotNone(reg.get("test-provider"))

    def test_official_cli_registered_with_backend(self):
        cfg = {"providers": {"codex-official": {"type": "official_cli", "cli": "codex", "models": ["gpt-5.6-luna"]}}}
        reg = self._fresh_with_providers(cfg)
        self.assertIsNotNone(reg.get("codex-official"))
        self.assertEqual(reg.get("codex-official").description, "provider 'codex-official' via codex_cli")


class ListProvidersTests(unittest.TestCase):
    def _build_registry_with_providers(self, cfg: dict) -> CliRegistry:
        import agent_broker_mcp as m

        reg = CliRegistry()
        from cli_backends import register_builtin_backends
        register_builtin_backends(reg)
        with mock.patch.object(m, "load_config", return_value=cfg):
            m._register_providers(reg)
        return reg

    def test_list_providers_output(self):
        import agent_broker_mcp as m

        cfg = {"providers": {"p1": {"type": "openai_compat", "base_url": "http://localhost:9999", "models": ["m1"]}}}
        reg = self._build_registry_with_providers(cfg)
        with mock.patch.object(m, "cli_registry", return_value=reg):
            with mock.patch.object(m, "providers_from_config_loaded", return_value=m.providers.providers_from_config(cfg)):
                lp = m.list_providers()
        self.assertEqual(lp["count"], 1)
        self.assertEqual(lp["providers"][0]["name"], "p1")
        self.assertEqual(lp["providers"][0]["type"], "openai_compat")
        self.assertIn("available", lp["providers"][0])
        self.assertIn("models", lp["providers"][0])

    def test_list_providers_reflects_auth(self):
        import agent_broker_mcp as m

        cfg = {"providers": {"claude-p": {"type": "official_cli", "cli": "claude", "models": ["opus"]}}}
        reg = self._build_registry_with_providers(cfg)
        with mock.patch.object(m, "cli_registry", return_value=reg):
            with mock.patch.object(m, "providers_from_config_loaded", return_value=m.providers.providers_from_config(cfg)):
                lp = m.list_providers()
        self.assertEqual(lp["providers"][0]["name"], "claude-p")
        self.assertIsInstance(lp["providers"][0]["available"], bool)


class ModelEnforcementTests(unittest.TestCase):
    def test_model_not_in_models_list_returns_error(self):
        import agent_broker_mcp as m

        # Create a mock backend with a closed models list
        b = mock.MagicMock(spec=CliBackend)
        b.name = "test-provider"
        b.metadata.return_value = {"models": ["m1", "m2"], "name": "test-provider"}

        cfg = {"providers": {"test-provider": {"type": "openai_compat", "base_url": "http://localhost:9999", "models": ["m1", "m2"]}}}
        with mock.patch.object(m, "load_config", return_value=cfg):
            result = m._enforce_provider_models(b, "m3")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "error")
        self.assertIn("model_not_available", result["error"])
        self.assertIn("m1", result["available_models"])

    def test_model_in_models_list_returns_none(self):
        import agent_broker_mcp as m

        b = mock.MagicMock(spec=CliBackend)
        b.name = "test-provider"
        b.metadata.return_value = {"models": ["m1", "m2"]}

        result = m._enforce_provider_models(b, "m1")
        self.assertIsNone(result)

    def test_no_models_list_returns_none(self):
        """When no closed models list is declared, any model is allowed."""
        import agent_broker_mcp as m

        b = mock.MagicMock(spec=CliBackend)
        b.metadata.return_value = {}

        result = m._enforce_provider_models(b, "any-model")
        self.assertIsNone(result)

    def test_no_target_model_returns_none(self):
        import agent_broker_mcp as m

        b = mock.MagicMock(spec=CliBackend)
        b.metadata.return_value = {"models": ["m1"]}

        self.assertIsNone(m._enforce_provider_models(b, None))
        self.assertIsNone(m._enforce_provider_models(b, ""))


class CcSwitchImportTests(unittest.TestCase):
    """Import cc-switch provider registry into our providers config."""

    def _make_db(self, tmpdir: str) -> str:
        """Create a minimal cc-switch-style SQLite db with claude + codex providers."""
        import sqlite3

        db_path = Path(tmpdir) / "cc-switch.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, settings_config TEXT)"
        )
        claude_relay = json.dumps({
            "env": {
                "ANTHROPIC_BASE_URL": "https://relay.example.com",
                "ANTHROPIC_AUTH_TOKEN": "sk-relay",
                "ANTHROPIC_MODEL": "claude-sonnet-4-6",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-6",
            }
        })
        claude_official = json.dumps({"env": {}})
        codex_relay = json.dumps({
            "auth": {"OPENAI_API_KEY": "sk-codex-relay"},
            "config": 'model_provider = "custom"\nmodel = "gpt-5.4"\n\n[model_providers.custom]\nbase_url = "https://codex-relay.example.com/v1"\n',
        })
        codex_official = json.dumps({
            "auth": {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {"id_token": "jwt"}},
            "config": "",
        })
        conn.executemany(
            "INSERT INTO providers VALUES (?,?,?,?)",
            [
                ("1", "claude", "relay-claude", claude_relay),
                ("2", "claude", "Claude Official", claude_official),
                ("3", "codex", "relay-codex", codex_relay),
                ("4", "codex", "OpenAI Official", codex_official),
            ],
        )
        conn.commit()
        conn.close()
        return str(db_path)

    def test_import_claude_and_codex_relays(self):
        import tempfile

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            db = self._make_db(td)
            block, key_env = providers.ccswitch_config_block(db)
        ps = block["providers"]
        # relay providers imported; official skipped
        self.assertIn("relay-claude", ps)
        self.assertIn("relay-codex", ps)
        self.assertNotIn("Claude Official", ps)
        self.assertNotIn("OpenAI Official", ps)
        # claude relay fields
        self.assertEqual(ps["relay-claude"]["base_url"], "https://relay.example.com")
        self.assertEqual(ps["relay-claude"]["api_key"], "sk-relay")
        self.assertIn("claude-sonnet-4-6", ps["relay-claude"]["models"])
        self.assertIn("claude-opus-4-6", ps["relay-claude"]["models"])
        # codex relay fields
        self.assertEqual(ps["relay-codex"]["base_url"], "https://codex-relay.example.com/v1")
        self.assertEqual(ps["relay-codex"]["api_key"], "sk-codex-relay")
        self.assertIn("gpt-5.4", ps["relay-codex"]["models"])
        # env_indirect=False (default): no env vars extracted
        self.assertEqual(key_env, {})

    def test_import_env_indirect_hides_keys(self):
        import tempfile

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            db = self._make_db(td)
            block, key_env = providers.ccswitch_config_block(db, env_indirect=True)
        ps = block["providers"]
        # config references env vars, secrets live in key_env only
        self.assertTrue(ps["relay-claude"]["api_key"].startswith("env:CCSWITCH_"))
        self.assertTrue(ps["relay-codex"]["api_key"].startswith("env:CCSWITCH_"))
        self.assertEqual(key_env["CCSWITCH_RELAY_CLAUDE_API_KEY"], "sk-relay")
        self.assertEqual(key_env["CCSWITCH_RELAY_CODEX_API_KEY"], "sk-codex-relay")
        # secrets never appear in the providers block
        self.assertNotIn("sk-relay", json.dumps(block))
        self.assertNotIn("sk-codex-relay", json.dumps(block))

    def test_write_ccswitch_env_file(self):
        import tempfile

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            target = providers.write_ccswitch_env_file(
                {"CCSWITCH_A_API_KEY": "sk-a", "CCSWITCH_B_API_KEY": "sk-b"},
                Path(td) / "ccswitch-keys.env",
            )
            self.assertTrue(target.is_file())
            text = target.read_text(encoding="utf-8")
            self.assertIn("CCSWITCH_A_API_KEY=sk-a", text)
            self.assertIn("CCSWITCH_B_API_KEY=sk-b", text)

    def test_import_missing_db_raises(self):
        with self.assertRaises(FileNotFoundError):
            providers.ccswitch_config_block("/nonexistent/cc-switch.db")


class CrossCliProviderTests(unittest.TestCase):
    """Cross-CLI x provider coupling: CLI executes a provider's model via injected env."""

    def _cfg_and_registry(self):
        import agent_broker_mcp as m

        cfg = {
            "providers": {
                "kimi": {
                    "type": "openai_compat",
                    "base_url": "https://api.kimi.com/coding",
                    "api_key": "sk-kimi-test",
                    "models": ["kimi-for-coding"],
                    "api_format": "both",
                },
                "deepseek": {
                    "type": "openai_compat",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-ds-test",
                    "models": ["deepseek-chat"],
                    "api_format": "openai",
                },
            }
        }
        reg = CliRegistry()
        from cli_backends import register_builtin_backends
        register_builtin_backends(reg)
        with mock.patch.object(m, "load_config", return_value=cfg):
            m._register_providers(reg)
        return cfg, reg

    def test_claude_cross_kimi(self):
        import agent_broker_mcp as m

        cfg, reg = self._cfg_and_registry()
        with mock.patch.object(m, "providers_from_config_loaded", return_value=providers.providers_from_config(cfg)):
            out = m._resolve_cross_cli_provider("claude_code", "kimi-for-coding", reg)
        self.assertIsNotNone(out)
        self.assertEqual(out[0], "kimi")
        self.assertIn("CLAUDE_CONFIG_DIR", out[1])
        import os
        self.assertTrue(os.path.isdir(out[1]["CLAUDE_CONFIG_DIR"]))
        self.assertTrue(os.path.isfile(os.path.join(out[1]["CLAUDE_CONFIG_DIR"], "settings.json")))

    def test_codex_cross_returns_none(self):
        import agent_broker_mcp as m

        cfg, reg = self._cfg_and_registry()
        with mock.patch.object(m, "providers_from_config_loaded", return_value=providers.providers_from_config(cfg)):
            out = m._resolve_cross_cli_provider("codex_cli", "kimi-for-coding", reg)
        self.assertIsNone(out)  # codex not env-overridable yet

    def test_protocol_mismatch_returns_none(self):
        import agent_broker_mcp as m

        cfg, reg = self._cfg_and_registry()
        with mock.patch.object(m, "providers_from_config_loaded", return_value=providers.providers_from_config(cfg)):
            # deepseek is openai-only; claude needs anthropic -> no match
            out = m._resolve_cross_cli_provider("claude_code", "deepseek-chat", reg)
        self.assertIsNone(out)

    def test_unknown_model_returns_none(self):
        import agent_broker_mcp as m

        cfg, reg = self._cfg_and_registry()
        with mock.patch.object(m, "providers_from_config_loaded", return_value=providers.providers_from_config(cfg)):
            out = m._resolve_cross_cli_provider("claude_code", "not-a-model", reg)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()