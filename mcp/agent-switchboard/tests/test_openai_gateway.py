"""Focused tests for the OpenAI-compatible HTTP gateway.

Tests use mock backends, not real HTTP servers, to avoid port conflicts and real
network calls. Full HTTP server smoke-tests are done manually (see the functional
test in ``docs/cli-adapter-gateway-survey.md``).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli_backend_base  # noqa: E402
from cli_backend_base import CliBackend, CliRegistry, CliResult  # noqa: E402
import openai_gateway as g  # noqa: E402


class EchoBackend(CliBackend):
    @property
    def name(self):
        return "echo"

    @property
    def aliases(self):
        return ["echo-alias"]

    def available(self):
        return True

    def metadata(self):
        return {"default_model": "defaultm", "models": ["echo-model-v1"]}

    def execute(self, prompt, model=None, timeout=300, **kw):
        return CliResult(
            response=f"ECHO:{prompt}",
            status="completed",
            backend=self.name,
            model_attested=True,
        )


class GatewayCoreTests(unittest.TestCase):
    def setUp(self):
        self.reg = CliRegistry()
        self.reg.register(EchoBackend())

    # -- model parsing ----------------------------------------------------------
    def test_parse_model_slash(self):
        self.assertEqual(g._parse_model_name("ollama/llama3"), ("ollama", "llama3"))

    def test_parse_model_bare(self):
        self.assertEqual(g._parse_model_name("llama3"), (None, "llama3"))

    def test_parse_model_empty(self):
        self.assertEqual(g._parse_model_name(""), (None, None))
        self.assertEqual(g._parse_model_name(None), (None, None))

    # -- resolve backend -------------------------------------------------------
    def test_resolve_explicit_backend(self):
        b, m = g._resolve_backend(self.reg, "echo", None)
        self.assertEqual(b.name, "echo")
        self.assertEqual(m, "defaultm")

    def test_resolve_explicit_backend_with_model(self):
        b, m = g._resolve_backend(self.reg, "echo", "my-model")
        self.assertEqual(b.name, "echo")
        self.assertEqual(m, "my-model")

    def test_resolve_bare_name_alias(self):
        b, m = g._resolve_backend(self.reg, None, "echo-alias")
        self.assertEqual(b.name, "echo")

    def test_resolve_bare_default_model(self):
        b, m = g._resolve_backend(self.reg, None, "defaultm")
        # defaultm matches the backend's default_model -> that backend
        self.assertEqual(b.name, "echo")
        self.assertEqual(m, "defaultm")

    def test_resolve_bare_models_list(self):
        b, m = g._resolve_backend(self.reg, None, "echo-model-v1")
        self.assertEqual(b.name, "echo")

    def test_resolve_unknown_model_raises(self):
        with self.assertRaises(ValueError) as ctx:
            g._resolve_backend(self.reg, None, "ghost")
        self.assertIn("model_not_found", str(ctx.exception))

    # -- messages to prompt ----------------------------------------------------
    def test_messages_to_prompt(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
            {"role": "user", "content": "           "},
        ]
        self.assertEqual(g._messages_to_prompt(msgs), "hello\nworld")

    def test_messages_to_prompt_empty(self):
        with self.assertRaises(ValueError):
            g._messages_to_prompt([])

    def test_messages_to_prompt_no_content(self):
        with self.assertRaises(ValueError):
            g._messages_to_prompt([{"role": "user", "content": ""}])

    # -- chat completion -------------------------------------------------------
    def test_chat_completion_calls_backend(self):
        with mock.patch.object(g, "_registry", return_value=self.reg):
            resp = g._chat_completion([{"role": "user", "content": "hi"}], "echo", 30)
        self.assertEqual(resp["choices"][0]["message"]["content"], "ECHO:hi")
        self.assertEqual(resp["object"], "chat.completion")
        self.assertIn("id", resp)
        self.assertIn("usage", resp)
        self.assertEqual(resp["usage"]["total_tokens"], 0)

    def test_chat_completion_unknown_model(self):
        with mock.patch.object(g, "_registry", return_value=self.reg):
            with self.assertRaises(ValueError) as ctx:
                g._chat_completion([{"role": "user", "content": "x"}], "ghost", 30)
        self.assertIn("model_not_found", str(ctx.exception))

    # -- handler methods -------------------------------------------------------
    def _mocked_handler(self, path, method, body=None):
        """Create a bare handler instance with mocked network/registry plumbing."""
        handler = g.OpenAICompatibleHandler.__new__(g.OpenAICompatibleHandler)
        handler.path = path
        handler.command = method
        handler.headers = {"Content-Length": str(len(body or b"{}"))}
        handler.wfile = mock.MagicMock()
        captured = {}

        def _send_json(status, payload):
            captured["status"] = status
            captured["payload"] = payload

        handler._send_json = _send_json

        def _read_body():
            if body is not None and len(body) > g.MAX_BODY_BYTES:
                raise ValueError("request_too_large")
            return body or b"{}"

        handler._read_body = _read_body
        with mock.patch.object(g, "_registry", return_value=self.reg):
            if method == "GET":
                handler.do_GET()
            else:
                handler.do_POST()
        return captured

    def test_health_endpoint(self):
        result = self._mocked_handler("/health", "GET")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["payload"]["status"], "ok")
        self.assertIn("echo", result["payload"]["route"])

    def test_unknown_path_returns_404(self):
        result = self._mocked_handler("/foo", "GET")
        self.assertEqual(result["status"], 404)

    # -- /v1/models ------------------------------------------------------------
    def test_list_models_from_registry(self):
        with mock.patch.object(g, "_registry", return_value=self.reg):
            resp = g._list_models()
        ids = [m["id"] for m in resp["data"]]
        self.assertEqual(resp["object"], "list")
        # echo backend contributes: echo, echo/defaultm, echo/echo-model-v1
        self.assertIn("echo", ids)
        self.assertIn("echo/defaultm", ids)
        self.assertIn("echo/echo-model-v1", ids)

    def test_list_models_dedupes(self):
        # Two backends that share the same default model name should produce distinct
        # prefixed ids, and a duplicate id should not be repeated.
        with mock.patch.object(g, "_registry", return_value=self.reg):
            resp = g._list_models()
        ids = [m["id"] for m in resp["data"]]
        self.assertEqual(len(ids), len(set(ids)))  # no duplicates

    def test_models_endpoint_get(self):
        result = self._mocked_handler("/v1/models", "GET")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["payload"]["object"], "list")
        self.assertIn("echo/defaultm", [m["id"] for m in result["payload"]["data"]])

    # -- provider integration via registry --------------------------------------
    def _reg_with_providers(self):
        """Return a registry with built-ins plus two providers."""
        import agent_broker_mcp as m
        from cli_backends import register_builtin_backends

        reg = CliRegistry()
        register_builtin_backends(reg)
        cfg = {
            "providers": {
                "p-openai": {"type": "openai_compat", "base_url": "http://localhost:1", "models": ["m1", "m2"]},
                "p-official": {"type": "official_cli", "cli": "codex", "models": ["gpt-5.6-luna"]},
            }
        }
        with mock.patch.object(m, "load_config", return_value=cfg):
            m._register_providers(reg)
        return reg

    def test_list_models_includes_providers(self):
        with mock.patch.object(g, "_registry", return_value=self._reg_with_providers()):
            resp = g._list_models()
        ids = [x["id"] for x in resp["data"]]
        self.assertIn("p-openai", ids)
        self.assertIn("p-openai/m1", ids)
        self.assertIn("p-openai/m2", ids)
        self.assertIn("p-official", ids)
        self.assertIn("p-official/gpt-5.6-luna", ids)

    def test_resolve_provider_backend(self):
        reg = self._reg_with_providers()
        with mock.patch.object(g, "_registry", return_value=reg):
            b, m = g._resolve_backend(reg, "p-openai", "m1")
        self.assertEqual(b.name, "p-openai")
        self.assertEqual(m, "m1")

    def test_resolve_provider_bare_model(self):
        reg = self._reg_with_providers()
        # p-openai's default_model is None — bare 'p-openai' resolves to the backend name.
        with mock.patch.object(g, "_registry", return_value=reg):
            b, m = g._resolve_backend(reg, None, "p-openai")
        self.assertEqual(b.name, "p-openai")

    def test_chat_completion_post(self):
        body = json.dumps({"model": "echo", "messages": [{"role": "user", "content": "t"}]}).encode()
        result = self._mocked_handler("/v1/chat/completions", "POST", body)
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["payload"]["choices"][0]["message"]["content"], "ECHO:t")

    def test_streaming_not_implemented(self):
        body = json.dumps({"model": "echo", "stream": True, "messages": [{"role": "user", "content": "x"}]}).encode()
        result = self._mocked_handler("/v1/chat/completions", "POST", body)
        self.assertEqual(result["status"], 400)
        self.assertIn("not_implemented", result["payload"]["error"]["code"])

    def test_bad_json_body(self):
        body = b"not json"
        result = self._mocked_handler("/v1/chat/completions", "POST", body)
        self.assertEqual(result["status"], 400)
        self.assertIn("invalid_request", result["payload"]["error"]["code"])


if __name__ == "__main__":
    unittest.main()