"""Run a disposable Codex session against a local scripted Responses endpoint."""

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

STEPS = (
    ("init", "--dry-run", "--no-drafts", "--json"),
    ("plan", "--changed", "--base", "main", "--json"),
    ("check", "--changed", "--focused", "--base", "main", "--json"),
    ("check", "drive", "--seed", "41", "--json"),
    ("report", "--format", "json"),
)


def prepare(root: Path, rung: str) -> Path:
    subprocess.run(["git", "init", "-qb", "main"], cwd=root, check=True)
    (root / "rung.toml").write_text("""
[project]
name = "agent smoke"

[[check]]
id = "api"
kind = "command"
run = "python3 verify.py"
verifies = ["app.py"]

[[check]]
id = "drive"
kind = "evidence"
run = "python3 drive.py"
require = [".txt"]
""")
    (root / ".gitignore").write_text(".rung/\n__pycache__/\n")
    (root / "app.py").write_text("refund = 100\n")
    (root / "verify.py").write_text("from app import refund\nassert refund == 100, 'refund exceeded charge'\n")
    (root / "drive.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "(Path(os.environ['RUNG_EVIDENCE_DIR']) / 'seed.txt').write_text(os.environ['RUNG_SEED'])\n"
    )
    subprocess.run([rung, "agents"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "baseline"], cwd=root, check=True,
    )  # fmt: skip
    (root / "app.py").write_text("refund = 150\n")
    nested = root / "nested"
    nested.mkdir()
    return nested


def smoke(root: Path, rung: str) -> None:
    nested = prepare(root, rung)
    outputs: dict[str, str] = {}
    found_skill = False
    turns = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            # Request bodies can contain agent instructions. Keep only fixture assertions.
            return

        def do_POST(self) -> None:
            nonlocal turns, found_skill
            if self.path.split("?")[0] != "/v1/responses":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            index = turns
            turns += 1
            context = json.dumps(body.get("input")) + str(body.get("instructions"))
            found_skill |= ".agents/skills/rung/SKILL.md" in context
            for entry in body.get("input", []):
                if entry.get("type") == "function_call_output":
                    outputs[entry["call_id"]] = str(entry["output"])

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()

            def event(kind: str, **data: object) -> None:
                payload = json.dumps({"type": kind, **data})
                self.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode())

            response = {"id": f"resp_{index}", "object": "response", "created_at": 1, "model": "fixture"}
            event("response.created", response={**response, "status": "in_progress", "output": []})
            if index < len(STEPS):
                tool = next(tool for tool in body["tools"] if tool.get("name") in {"exec_command", "shell_command"})
                props = tool.get("parameters", {}).get("properties", {})
                args = {"cmd" if "cmd" in props else "command": shlex.join([rung, *STEPS[index]])}
                if "workdir" in props:
                    args["workdir"] = str(nested)
                item = {
                    "type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
                    "name": tool["name"], "arguments": json.dumps(args), "status": "completed",
                }  # fmt: skip
                event(
                    "response.output_item.added",
                    output_index=0,
                    item={**item, "arguments": "", "status": "in_progress"},
                )
                event("response.function_call_arguments.delta", item_id=item["id"], output_index=0,
                      delta=item["arguments"])  # fmt: skip
            else:
                message = "Fixture session complete."
                item = {
                    "type": "message", "id": f"msg_{index}", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": message, "annotations": []}],
                }  # fmt: skip
                event(
                    "response.output_item.added", output_index=0, item={**item, "content": [], "status": "in_progress"}
                )
                event("response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta=message)
            event("response.output_item.done", output_index=0, item=item)
            event("response.completed", response={
                **response, "status": "completed", "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            })  # fmt: skip

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        config = [
            'approval_policy="never"',
            'model_provider="fixture"',
            f'projects.{json.dumps(str(root))}.trust_level="trusted"',
            'model_providers.fixture.name="Fixture"',
            f'model_providers.fixture.base_url="http://127.0.0.1:{server.server_port}/v1"',
            'model_providers.fixture.wire_api="responses"',
            "model_providers.fixture.requires_openai_auth=false",
            'model_providers.fixture.env_key="RUNG_CODEX_SMOKE_KEY"',
        ]
        command = [
            "codex", "exec", "--ignore-user-config", "--ephemeral", "--json", "--sandbox", "workspace-write",
            "-C", str(nested), "--add-dir", str(root), "-m", "fixture",
        ]  # fmt: skip
        for setting in config:
            command += ["-c", setting]
        command.append("Use $rung to verify this branch and report the result.")
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=100,
                                    env={**os.environ, "RUNG_CODEX_SMOKE_KEY": "fixture"})  # fmt: skip
        finally:
            server.shutdown()

    if result.returncode:
        raise SystemExit(f"Codex exited {result.returncode}: {result.stderr[-2000:]}")
    expected = ('"dry_run": true', '"focused_run"', "refund exceeded charge", '"recorded": false', '"ledger_intact"')
    for i, marker in enumerate(expected):
        if marker not in outputs.get(f"call_{i}", ""):
            raise SystemExit(f"Codex did not return the expected fixture evidence for rung {' '.join(STEPS[i])}")
    if not found_skill:
        raise SystemExit("Codex did not discover the project-local Rung skill")
    print("Codex discovered Rung and executed preview, plan, failed check, seed replay and report.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rung", default="rung", help="path to the Rung executable")
    args = parser.parse_args()
    rung = shutil.which(args.rung)
    if not rung or not shutil.which("codex"):
        parser.error("install Rung and Codex or pass the local Rung executable with --rung")
    with tempfile.TemporaryDirectory(prefix="rung-codex-smoke-") as directory:
        smoke(Path(directory).resolve(), str(Path(rung).absolute()))


if __name__ == "__main__":
    main()
