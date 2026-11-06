"""Run a disposable Claude Code session against a local scripted Messages endpoint."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from smoke_codex import STEPS, prepare


def smoke(root: Path, rung: str) -> None:
    prepare(root, rung)
    subprocess.run([rung, "agents", "--hooks"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    outputs: dict[str, str] = {}
    found_skill = False
    stopped_for_verification = False
    turns = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            nonlocal turns, found_skill, stopped_for_verification
            if self.path.split("?")[0] != "/v1/messages":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if not body.get("stream"):
                self.send_error(400)
                return
            turns += 1
            context = json.dumps(body)
            found_skill |= "# Working with Rung" in context
            stopped_for_verification |= "for touched checks without fresh passing verdicts." in context
            for message in body.get("messages", []):
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_result":
                            outputs[block["tool_use_id"]] = json.dumps(block.get("content"))

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()

            def event(kind: str, **data: object) -> None:
                payload = json.dumps({"type": kind, **data})
                self.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode())

            event("message_start", message={
                "id": f"msg_{turns}", "type": "message", "role": "assistant", "model": body["model"],
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            })  # fmt: skip
            index = len(outputs)
            if index < len(STEPS):
                args = {"command": shlex.join([rung, *STEPS[index]]), "description": f"Run Rung {STEPS[index][0]}"}
                block = {"type": "tool_use", "id": f"toolu_{index}", "name": "Bash", "input": {}}
                delta = {"type": "input_json_delta", "partial_json": json.dumps(args)}
                stop_reason = "tool_use"
            else:
                block = {"type": "text", "text": ""}
                delta = {"type": "text_delta", "text": "Fixture session complete."}
                stop_reason = "end_turn"
            event("content_block_start", index=0, content_block=block)
            event("content_block_delta", index=0, delta=delta)
            event("content_block_stop", index=0)
            event(
                "message_delta", delta={"stop_reason": stop_reason, "stop_sequence": None}, usage={"output_tokens": 1}
            )
            event("message_stop")

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "TMPDIR", "LANG", "SHELL") if key in os.environ}
        env.update(
            ANTHROPIC_API_KEY="fixture",
            ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
            CLAUDE_CONFIG_DIR=str(root / ".rung" / "claude-config"),
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
            PATH=os.pathsep.join((str(Path(rung).parent), env.get("PATH", os.defpath))),
        )
        command = [
            "claude", "--setting-sources", "project", "-p", "--no-session-persistence", "--strict-mcp-config",
            "--tools", "Bash", "--allowedTools", "Bash", "--permission-mode", "dontAsk", "--model", "claude-sonnet-4-6",
            "/rung Verify this branch and report the result.",
        ]  # fmt: skip
        try:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=100)
        finally:
            server.shutdown()

    if result.returncode:
        raise SystemExit(f"Claude Code exited {result.returncode}: {result.stderr[-2000:]}")
    expected = ('"dry_run": true', '"focused_run"', "refund exceeded charge", '"recorded": false', '"ledger_intact"')
    for index, marker in enumerate(expected):
        # Messages can carry tool output as either text or an array of content blocks.
        output = outputs.get(f"toolu_{index}", "").replace(r"\"", '"')
        if marker not in output:
            raise SystemExit(
                f"Claude Code did not return the expected fixture evidence for rung {' '.join(STEPS[index])}"
            )
    if not found_skill:
        raise SystemExit("Claude Code did not load the project-local Rung skill")
    if not stopped_for_verification:
        raise SystemExit("Claude Code did not receive the Rung Stop hook's verification request")
    print("Claude Code loaded Rung, ran all five workflow commands and received the Rung Stop hook request.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rung", default="rung", help="path to the Rung executable")
    args = parser.parse_args()
    rung = shutil.which(args.rung)
    if not rung or not shutil.which("claude"):
        parser.error("install Rung and Claude Code or pass the local Rung executable with --rung")
    with tempfile.TemporaryDirectory(prefix="rung-claude-smoke-") as directory:
        smoke(Path(directory).resolve(), str(Path(rung).absolute()))


if __name__ == "__main__":
    main()
