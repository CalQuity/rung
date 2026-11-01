"""Model providers: the model that drafts specs, proofs and drive scripts.

Every call is schema-constrained: the model must return one JSON object matching a JSON Schema,
which the provider's own structured-output feature enforces. Claude Code gets the schema with
`--json-schema`, Codex with `--output-schema`, OpenAI-compatible servers with `response_format`.
Nothing is scraped out of free text.

Rung never asks for or stores credentials. The CLI providers reuse the login of a tool the
developer already has (`claude auth login`, `codex login`); the HTTP provider reads its key from
an environment variable. The model only *proposes*: a checker (TLC, the Lean kernel, a
differential test, the app itself) accepts or rejects what it drafts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rung.core.config import LLMConfig
from rung.core.errors import RungError, ToolMissing

PROVIDERS = {
    "claude-cli": "Claude Code CLI (uses your `claude auth login` session)",
    "codex-cli": "Codex CLI (uses your `codex login` session)",
    "openai-compatible": "Any OpenAI-compatible /v1/chat/completions endpoint (Ollama, vLLM, Kompact, ...)",
    "none": "No model; Rung runs checks but drafts nothing",
}


class ProviderError(RungError):
    """The model could not be reached, or did not return the requested JSON."""


@dataclass
class LoginState:
    ok: bool
    detail: str


def check_shape(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Verify the parts of a JSON Schema Rung uses (object, array, string, required).

    The providers enforce the schema already; this guards against a provider that
    silently ignores it, so a malformed reply fails here instead of deep in a draft.
    """
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ProviderError(f"{path}: expected an object")
        for key in schema.get("required", []):
            if key not in value:
                raise ProviderError(f"{path}: missing `{key}`")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                check_shape(value[key], sub, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise ProviderError(f"{path}: expected an array")
        for i, item in enumerate(value):
            check_shape(item, schema.get("items", {}), f"{path}[{i}]")
    elif kind == "string" and not isinstance(value, str):
        raise ProviderError(f"{path}: expected a string")


class Provider:
    name = "none"

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.models_used: set[str] = set()  # the models that actually answered, as each tool reports them

    def models(self) -> str:
        """The models that answered so far, else the configured one, else the CLI's own default."""
        return ", ".join(sorted(self.models_used)) or self.config.model or "the CLI's default model"

    def available(self) -> bool:
        return False

    def login_state(self) -> LoginState:
        return LoginState(False, "no provider configured")

    def login(self) -> int:
        """Run the provider's own interactive login; returns its exit code."""
        return 1

    def complete_json(
        self, prompt: str, *, schema: dict[str, Any], system: str = "", cwd: Path | None = None, timeout: int = 1200
    ) -> dict[str, Any]:
        """Ask the model for one JSON object matching `schema`."""
        raise ToolMissing("no model provider is configured; run `rung init` or set [llm] in rung.toml")


class ClaudeCLI(Provider):
    name = "claude-cli"

    def available(self) -> bool:
        return shutil.which("claude") is not None

    def login_state(self) -> LoginState:
        if not self.available():
            return LoginState(False, "`claude` is not installed")
        proc = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=60)
        try:
            info = json.loads(proc.stdout)
        except json.JSONDecodeError:
            lines = (proc.stdout or proc.stderr).strip().splitlines()
            return LoginState(proc.returncode == 0, lines[0] if lines else "")
        # Report only the login state, never account identifiers such as the email.
        if info.get("loggedIn"):
            return LoginState(True, f"logged in ({info.get('authMethod', 'unknown method')})")
        return LoginState(False, "not logged in")

    def login(self) -> int:
        return subprocess.call(["claude", "auth", "login"])

    def complete_json(
        self, prompt: str, *, schema: dict[str, Any], system: str = "", cwd: Path | None = None, timeout: int = 1200
    ) -> dict[str, Any]:
        if not self.available():
            raise ToolMissing("`claude` is not installed")
        # Drafting only: with no tools the model reads nothing and runs nothing on its own.
        cmd = ["claude", "-p", "--tools", "", "--output-format", "json", "--json-schema", json.dumps(schema)]
        if self.config.model:
            cmd += ["--model", self.config.model]
        if system:
            cmd += ["--append-system-prompt", system]
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:500]}"
            ) from exc
        if envelope.get("is_error"):
            raise ProviderError(f"claude reported an error: {str(envelope.get('result'))[:500]}")
        self.models_used.update(envelope.get("modelUsage") or {})
        result = envelope.get("structured_output")
        if result is None:
            raise ProviderError("claude returned no structured output")
        check_shape(result, schema)
        return result


class CodexCLI(Provider):
    name = "codex-cli"

    def available(self) -> bool:
        return shutil.which("codex") is not None

    def login_state(self) -> LoginState:
        if not self.available():
            return LoginState(False, "`codex` is not installed")
        proc = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=60)
        detail = (proc.stdout or proc.stderr).strip().splitlines()
        return LoginState(proc.returncode == 0, detail[0] if detail else "")

    def login(self) -> int:
        return subprocess.call(["codex", "login"])

    def complete_json(
        self, prompt: str, *, schema: dict[str, Any], system: str = "", cwd: Path | None = None, timeout: int = 1200
    ) -> dict[str, Any]:
        if not self.available():
            raise ToolMissing("`codex` is not installed")
        full = f"{system}\n\n{prompt}" if system else prompt
        with tempfile.TemporaryDirectory(prefix="rung-codex-") as tmp:
            out, schema_file = Path(tmp) / "last.json", Path(tmp) / "schema.json"
            schema_file.write_text(json.dumps(schema))
            # Read-only sandbox: Codex may read the repository but cannot change or run anything in it.
            cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                   "--output-schema", str(schema_file), "-o", str(out)]  # fmt: skip
            if self.config.model:
                cmd += ["-m", self.config.model]
            cmd.append("-")
            proc = subprocess.run(cmd, input=full, cwd=cwd or tmp, capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0:
                raise ProviderError(f"codex exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
            try:
                result = json.loads(out.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ProviderError("codex did not return the requested JSON") from exc
        # Codex does not report which model answered; record what was requested, and say so.
        if model := self.config.model or _codex_default_model():
            self.models_used.add(f"{model}, as configured")
        check_shape(result, schema)
        return result


def _codex_default_model() -> str:
    """The model `codex exec` uses without `-m`, from its config (with the reasoning effort, if set)."""
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    try:
        config = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    model = str(config.get("model") or "")
    effort = config.get("model_reasoning_effort")
    return f"{model} ({effort} effort)" if model and effort else model


class OpenAICompatible(Provider):
    name = "openai-compatible"

    def available(self) -> bool:
        return bool(self.config.base_url)

    def login_state(self) -> LoginState:
        if not self.config.base_url:
            return LoginState(False, "set [llm] base_url in rung.toml")
        has_key = bool(os.environ.get(self.config.api_key_env))
        return LoginState(True, f"{self.config.base_url} (key from ${self.config.api_key_env}: "
                                f"{'set' if has_key else 'not set'})")  # fmt: skip

    def login(self) -> int:
        print(f"Export your key as ${self.config.api_key_env}; Rung never stores it.")
        return 0

    def _post(self, body: dict[str, Any], timeout: int) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if key := os.environ.get(self.config.api_key_env):
            headers["Authorization"] = f"Bearer {key}"
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read())

    def complete_json(
        self, prompt: str, *, schema: dict[str, Any], system: str = "", cwd: Path | None = None, timeout: int = 1200
    ) -> dict[str, Any]:
        if not self.config.base_url:
            raise ToolMissing("set [llm] base_url for the openai-compatible provider")
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body: dict[str, Any] = {
            "model": self.config.model or "default",
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rung", "schema": schema, "strict": True},
            },
        }
        try:
            payload = self._post(body, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 422):
                raise ProviderError(f"{self.config.base_url} returned HTTP {exc.code}") from exc
            # The server does not support json_schema: ask in the prompt and extract the object.
            body.pop("response_format")
            messages[-1]["content"] += "\n\nReply with only one JSON object matching this schema:\n" + json.dumps(
                schema
            )
            payload = self._post(body, timeout)
        except urllib.error.URLError as exc:
            raise ProviderError(f"cannot reach {self.config.base_url}: {exc.reason}") from exc
        if payload.get("model"):
            self.models_used.add(str(payload["model"]))
        content = payload["choices"][0]["message"]["content"]
        match = re.search(r"\{[\s\S]*\}", content)
        try:
            result = json.loads(match.group(0) if match else content)
        except json.JSONDecodeError as exc:
            raise ProviderError("the model did not return valid JSON") from exc
        check_shape(result, schema)
        return result


_REGISTRY: dict[str, type[Provider]] = {
    "claude-cli": ClaudeCLI,
    "codex-cli": CodexCLI,
    "openai-compatible": OpenAICompatible,
    "none": Provider,
}


def get(config: LLMConfig) -> Provider:
    cls = _REGISTRY.get(config.provider)
    if cls is None:
        raise ValueError(f"unknown provider {config.provider!r}; choose one of {', '.join(PROVIDERS)}")
    return cls(config)


def detect() -> list[str]:
    """Providers usable on this machine right now, best first."""
    found = [name for name, cls in (("claude-cli", ClaudeCLI), ("codex-cli", CodexCLI)) if cls(LLMConfig()).available()]
    return [*found, "openai-compatible", "none"]
