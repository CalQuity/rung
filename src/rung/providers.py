"""LLM providers: the model that drafts specs, properties and explanations.

Rung never asks for or stores credentials itself. The CLI providers reuse the
login of a tool the developer already has (`claude auth login`, `codex login`);
the HTTP provider reads its key from an environment variable. The model only
*proposes*: every artifact it drafts is accepted or rejected by a checker
(TLC, the Lean kernel, a differential test), never by the model's own word.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rung.config import LLMConfig
from rung.errors import ToolMissing

PROVIDERS = {
    "claude-cli": "Claude Code CLI (uses your `claude auth login` session)",
    "codex-cli": "Codex CLI (uses your `codex login` session)",
    "openai-compatible": "Any OpenAI-compatible /v1/chat/completions endpoint (Ollama, vLLM, Kompact, ...)",
    "none": "No model; Rung runs checks but drafts nothing",
}


@dataclass
class LoginState:
    ok: bool
    detail: str


class Provider:
    name = "none"

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return False

    def login_state(self) -> LoginState:
        return LoginState(False, "no provider configured")

    def login(self) -> int:
        """Run the provider's own interactive login; returns its exit code."""
        return 1

    def complete(self, prompt: str, *, system: str = "", cwd: Path | None = None, timeout: int = 900) -> str:
        raise ToolMissing("no LLM provider is configured; run `rung init` or set [llm] in rung.toml")


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

    def complete(self, prompt: str, *, system: str = "", cwd: Path | None = None, timeout: int = 900) -> str:
        if not self.available():
            raise ToolMissing("`claude` is not installed")
        # Drafting only: the model reads nothing and runs nothing on its own.
        cmd = ["claude", "-p", "--output-format", "text", "--tools", ""]
        if self.config.model:
            cmd += ["--model", self.config.model]
        if system:
            cmd += ["--append-system-prompt", system]
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        return proc.stdout.strip()


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

    def complete(self, prompt: str, *, system: str = "", cwd: Path | None = None, timeout: int = 900) -> str:
        if not self.available():
            raise ToolMissing("`codex` is not installed")
        full = f"{system}\n\n{prompt}" if system else prompt
        with tempfile.TemporaryDirectory(prefix="rung-codex-") as tmp:
            out = Path(tmp) / "last.md"
            cmd = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "-s",
                "read-only",
                "-o",
                str(out),
            ]
            if self.config.model:
                cmd += ["-m", self.config.model]
            cmd.append("-")
            proc = subprocess.run(
                cmd, input=full, cwd=cwd or tmp, capture_output=True, text=True, timeout=timeout
            )
            if proc.returncode != 0:
                raise RuntimeError(f"codex exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
            return out.read_text().strip() if out.exists() else proc.stdout.strip()


class OpenAICompatible(Provider):
    name = "openai-compatible"

    def available(self) -> bool:
        return bool(self.config.base_url)

    def login_state(self) -> LoginState:
        if not self.config.base_url:
            return LoginState(False, "set [llm] base_url in rung.toml")
        has_key = bool(os.environ.get(self.config.api_key_env))
        return LoginState(
            True,
            f"{self.config.base_url} (key from ${self.config.api_key_env}: "
            f"{'set' if has_key else 'not set'})",
        )

    def login(self) -> int:
        print(f"Export your key as ${self.config.api_key_env}; Rung never stores it.")
        return 0

    def complete(self, prompt: str, *, system: str = "", cwd: Path | None = None, timeout: int = 900) -> str:
        if not self.config.base_url:
            raise ToolMissing("set [llm] base_url for the openai-compatible provider")
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        body = json.dumps({"model": self.config.model or "default", "messages": messages}).encode()
        headers = {"Content-Type": "application/json"}
        if key := os.environ.get(self.config.api_key_env):
            headers["Authorization"] = f"Bearer {key}"
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{url} returned {exc.code}: {exc.read()[:300]!r}") from exc
        return payload["choices"][0]["message"]["content"].strip()


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
    found = [
        name
        for name, cls in (("claude-cli", ClaudeCLI), ("codex-cli", CodexCLI))
        if cls(LLMConfig()).available()
    ]
    return [*found, "openai-compatible", "none"]
