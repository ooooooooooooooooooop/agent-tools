"""generic.py — configuration-driven adapter for whatever local CLI a user has.

Lets a user register an arbitrary local CLI purely from ``config.json`` (no Python
code), satisfying the "flexibly take any local CLI" requirement:

    "cli_backends": {
      "ollama": {
        "command": "ollama",
        "args_template": ["run", "{model}"],
        "prompt_arg": "{prompt}",
        "model_aliases": {"llama3": "llama3:70b"},
        "default_model": "llama3:70b",
        "description": "Local Ollama",
        "output_format": "plain_text"
      }
    }

Security: only four placeholders are substituted into ``args_template`` and
``prompt_arg`` (\\{prompt\\}, \\{model\\}, \\{effort\\}, \\{timeout\\}). No shell is
involved — the command is built as an argv array and passed to subprocess directly, so
there is no command-injection via model/prompt text. A config that names a malicious
executable is the user's own choice, matching how Switchboard already trusts
``codex_path``/``claude_path``.
"""

from __future__ import annotations

from typing import Any

from cli_backend_base import (
    CliBackend,
    CliResult,
    shell_split_field,
    substitute_template,
)


class GenericCliBackend(CliBackend):
    def __init__(self, name: str, spec: dict[str, Any]) -> None:
        self._name = str(name or "").strip()
        if not self._name:
            raise ValueError("GenericCliBackend name must not be empty")
        spec = spec or {}
        # Backend type: "cli" (argv subprocess) or "http"/"openai_compat" (OpenAI-compatible
        # local serve over HTTP, e.g. Ollama / LM Studio / vLLM).
        raw_type = str(spec.get("type") or spec.get("protocol") or "cli").strip().lower()
        self._backend_type = "http" if raw_type in {"http", "openai", "openai_compat", "openai-compatible"} else "cli"
        self._base_url = str(spec.get("base_url") or "").rstrip("/")
        self._api_key = spec.get("api_key") or ""  # optional; local serve usually needs none
        self._command = spec.get("command", self._name)
        self._args_template = spec.get("args_template") or []
        if isinstance(self._args_template, str):
            self._args_template = shell_split_field(self._args_template)
        elif not isinstance(self._args_template, list):
            self._args_template = []
        self._prompt_arg = spec.get("prompt_arg") or "{prompt}"
        self._prompt_position = spec.get("prompt_position")  # "before" | "after" (argv tail default)
        self._model_aliases = spec.get("model_aliases") or {}
        self._default_model = spec.get("default_model")
        self._models = [str(x) for x in (spec.get("models") or []) if str(x).strip()]
        self._description = spec.get("description") or ""
        self._output_format = str(spec.get("output_format") or "plain_text").lower()
        self._json_path = spec.get("json_path")  # e.g. "response" or "message.content"
        self._aliases = spec.get("aliases") or []
        if isinstance(self._aliases, str):
            self._aliases = [self._aliases]
        self._timeout = int(spec.get("timeout") or 300)
        self._supports_model = bool(spec.get("supports_model", True))
        self._supports_effort = bool(spec.get("supports_effort", False))
        self._extra_env = spec.get("env") or {}
        self._discovery = spec.get("discovery")  # optional explicit executable path or list
        self._extra_capabilities = spec.get("capabilities") or []

    # -- identities --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def aliases(self) -> list[str]:
        return [str(a).strip() for a in self._aliases if str(a).strip()]

    @property
    def family(self) -> str:
        return "custom"

    @property
    def backend_type(self) -> str:
        return self._backend_type

    @property
    def description(self) -> str:
        return self._description or (
            f"Generic {self._backend_type} backend '{self._name}'"
        )

    @property
    def capabilities(self) -> list[str]:
        caps = ["chat"]
        if isinstance(self._model_aliases, dict) and getattr(self, "_supports_model", True):
            caps.append("models")
        if self._backend_type == "http":
            caps.extend(["json", "reasoning"])
        extra = [str(c) for c in self._extra_capabilities if str(c)]
        caps.extend(extra)
        return caps

    # -- discovery ---------------------------------------------------------
    def _command_vector(self) -> list[str]:
        if isinstance(self._command, list):
            return [str(c) for c in self._command]
        return shell_split_field(self._command) or [self._name]

    # -- command building --------------------------------------------------
    def build_command(
        self,
        executable: str,
        prompt: str,
        model: str | None = None,
        effort: str | None = None,
        mode: str = "read-only",
        project_root: str | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> list[str]:
        # The resolved executable always heads the argv; the configured command tail
        # (everything after the head in `command`, plus any static `args_template`
        # tokens) follows. This keeps e.g. `command: ["python", "tool.py"]` correct
        # while allowing discovery to resolve a full path for the head.
        vector = self._command_vector()
        cmd: list[str] = [executable]
        if vector:
            cmd.extend(vector[1:])
        resolved_model = self.resolve_model(model) if model else model
        templated = substitute_template(
            [str(t) for t in self._args_template],
            prompt="",
            model=resolved_model,
            effort=effort,
            timeout=timeout,
            project_root=project_root,
        )
        cmd.extend(templated)
        prompt_text = str(self._prompt_arg)
        prompt_text = prompt_text.replace("{model}", resolved_model or "")
        prompt_text = prompt_text.replace("{effort}", effort or "")
        prompt_text = prompt_text.replace("{timeout}", str(int(timeout)))
        prompt_text = prompt_text.replace("{prompt}", prompt)
        if self._prompt_position == "before":
            cmd.insert(0, prompt_text)
        else:
            cmd.append(prompt_text)
        return cmd

    def resolve_model(self, alias: str, context: dict | None = None) -> str:
        if not alias:
            return self._default_model or ""
        aliases = self._model_aliases or {}
        if isinstance(aliases, dict) and alias in aliases:
            return str(aliases[alias])
        return alias

    # -- parsing -----------------------------------------------------------
    def parse_output(self, stdout: str, stderr: str, exit_code: int) -> CliResult:
        status = "completed" if exit_code == 0 else "error"
        response = stdout
        if self._output_format == "json":
            response = self._extract_json(stdout, stderr)
        elif self._output_format == "stdout" or not response:
            response = stdout or stderr or "(no output)"
        else:
            response = (stdout or stderr or "(no output)").strip()
        return CliResult(
            response=response,
            status=status,
            exit_code=exit_code,
            raw_stdout=stdout,
            raw_stderr=stderr,
            backend=self.name,
        )

    def _extract_json(self, stdout: str, stderr: str) -> str:
        import json

        raw = stdout or stderr
        data: Any = None
        for candidate in (stdout, stderr):
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                break
            except (json.JSONDecodeError, TypeError):
                continue
        if data is None:
            hint = (stdout or stderr or "").strip()
            return "(no JSON output)" + (f": {hint[:200]}" if hint else "")
        if self._json_path:
            node = data
            for part in str(self._json_path).split("."):
                if isinstance(node, dict):
                    node = node.get(part)
                elif isinstance(node, list) and part.isdigit():
                    node = node[int(part)]
                else:
                    node = None
                    break
            return str(node) if node is not None else "(json path not found)"
        if isinstance(data, dict):
            for key in ("response", "answer", "text", "content", "message", "output"):
                if key in data and isinstance(data[key], str):
                    return data[key]
        return json.dumps(data, ensure_ascii=False)

    # -- metadata ----------------------------------------------------------
    def metadata(self) -> dict[str, Any]:
        md = super().metadata()
        md.update(
            {
                "supports_model": self._supports_model,
                "supports_effort": self._supports_effort,
                "default_model": self._default_model,
                "models": list(self._models),
                "timeout": self._timeout,
                "output_format": self._output_format,
                "base_url": self._base_url or None,
            }
        )
        return md

    # -- availability ------------------------------------------------------
    def discover(self) -> str | None:
        # For an HTTP backend there is no executable to discover; availability is
        # "configured with a base_url" (liveness is checked at execute time).
        if self._backend_type == "http":
            return self._base_url or None
        import os
        import shutil

        if self._discovery:
            candidates = (
                [self._discovery]
                if isinstance(self._discovery, str)
                else list(self._discovery)
            )
            for cand in candidates:
                cand_str = str(cand)
                if os.path.isfile(cand_str):
                    return cand_str
        vector = self._command_vector()
        head = vector[0]
        if "/" in head or "\\" in head:
            if os.path.isfile(head):
                return head
        found = shutil.which(head)
        return found or (head if os.path.isfile(head) else None)

    # -- execution ---------------------------------------------------------
    def execute(
        self,
        prompt: str,
        model: str | None = None,
        effort: str | None = None,
        mode: str = "read-only",
        project_root: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> CliResult:
        effective_timeout = timeout or self._timeout
        # HTTP openai_compat backend talks to a local serve endpoint, no subprocess.
        if self._backend_type == "http":
            return self._execute_http(prompt, model, effective_timeout)
        exe = self.discover()
        if not exe:
            return CliResult(
                response=f"{self._name} CLI was not found.",
                status="cli_not_found",
                requested_model=model,
                requested_effort=effort,
                backend=self.name,
            )
        command = self.build_command(
            exe,
            prompt,
            model=model,
            effort=effort,
            mode=mode,
            project_root=project_root,
            timeout=effective_timeout,
            **kwargs,
        )
        runner = kwargs.get("runner")
        if runner is None:
            from agent_broker_mcp import run_process

            runner = lambda c, cwd, timeout=effective_timeout: run_process(  # noqa: E731
                c, cwd or str(project_root or ""), timeout=timeout
            )
        exit_code, stdout, stderr = runner(command, project_root, timeout=effective_timeout)
        result = self.parse_output(stdout, stderr, exit_code)
        result.requested_model = model
        result.requested_effort = effort
        return result

    def _execute_http(self, prompt: str, model: str | None, timeout: int) -> CliResult:
        """Call an OpenAI-compatible local HTTP endpoint (/v1/chat/completions).

        Requires ``base_url`` (e.g. ``http://localhost:11434/v1``). Uses ``urllib``
        (stdlib) like the broker's existing HTTP/Gemini path — no httpx dependency.
        """
        import json as _json
        import urllib.error
        import urllib.request

        base = self._base_url or "http://localhost:11434/v1"
        # Normalise: if base doesn't end with /v1, append it so the full path becomes
        # /v1/chat/completions (OpenAI and most relays expect this).  A bare endpoint
        # like "https://api.kimi.com/coding" would otherwise become
        # "https://api.kimi.com/coding/chat/completions", which is wrong.
        base = base.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        url = f"{base}/chat/completions"
        resolved_model = self.resolve_model(model) or self._default_model
        if not resolved_model:
            return CliResult(
                response=f"{self._name} is an http backend but no model was specified.",
                status="error",
                requested_model=model,
                backend=self.name,
                exit_code=-1,
            )
        payload = {
            "model": resolved_model,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:  # noqa: BLE001
                pass
            return CliResult(
                response=f"{self._name} HTTP {exc.code} from {url}: {detail}".strip(),
                status="error",
                requested_model=resolved_model,
                backend=self.name,
                exit_code=exc.code or -1,
            )
        except Exception as exc:  # noqa: BLE001
            return CliResult(
                response=f"{self._name} HTTP error from {url}: {type(exc).__name__}: {exc}",
                status="error",
                requested_model=resolved_model,
                backend=self.name,
                exit_code=-1,
            )
        try:
            data = _json.loads(raw)
            reply = (data["choices"][0]["message"]["content"] or "").strip()
            return CliResult(
                response=reply or "(empty reply)",
                status="completed",
                requested_model=resolved_model,
                backend=self.name,
                metadata={"model": data.get("model"), "provider": "openai_compat"},
            )
        except (KeyError, IndexError, ValueError) as exc:
            return CliResult(
                response=f"{self._name} HTTP response was not OpenAI-compatible: {type(exc).__name__}: {raw[:200]}",
                status="error",
                requested_model=resolved_model,
                backend=self.name,
                exit_code=-1,
            )