"""Execute configured checks and export their health as an offline HTML page."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter

from rung.analysis import score
from rung.checks import runner
from rung.cli.commands.common import GATE_FAILED, OK, load_config, resolve_base
from rung.cli.output import Output
from rung.core.config import ConfigError, write_atomic
from rung.core.redact import redact
from rung.engines import score_page

DEFAULT_OUTPUT = Path(".rung/cache/score/index.html")


def cmd_score_my_code(args, out: Output) -> int:
    """Run all declared checks, or report saved results, without drafting or setup."""
    cfg = load_config(args)
    output = _output_path(cfg.root, args.out)
    base = resolve_base(cfg.root, args.base) if args.base else None
    durations: dict[str, float] = {}
    elapsed = None
    out.heading("Verification report", f"{cfg.name} · configured-check health")
    if not args.no_run:
        start = perf_counter()
        for index, check in enumerate(cfg.checks, 1):
            out.progress(f"[{index}/{len(cfg.checks)}] Running {check.id}")
            before = perf_counter()
            result = runner.run_check(cfg, check, base=base)
            durations[check.id] = round(perf_counter() - before, 3)
            out.line(redact(f"{result.verdict.upper()}  {check.id}: {result.outcome.summary}"))
        elapsed = round(perf_counter() - start, 3)
    data = score.build(cfg, ran_checks=not args.no_run, elapsed_seconds=elapsed, durations=durations, base=base)
    # Recheck after repository commands ran: they may have changed the destination.
    output = _output_path(cfg.root, str(output))
    write_atomic(output, score_page.render(data))
    data["report_path"] = str(output)
    out.emit(data)
    value = data["score"]["value"]
    out.line(f"{data['stats']['fresh']} of {data['stats']['total']} configured checks passed with current evidence.")
    out.line(
        f"Verification health: {value}/100 (Rung score; configured checks only)"
        if value is not None
        else "Not scored: no checks configured"
    )
    out.line("This is not an overall code-quality score. Missing areas are listed in the report.")
    out.location(str(output), label="HTML report: ", indent=0)
    return OK if data["passed"] else GATE_FAILED


def _output_path(root: Path, requested: str | None) -> Path:
    """Keep reports away from project inputs and refuse links or unrelated existing files."""
    candidate = Path(requested).expanduser() if requested else root / DEFAULT_OUTPUT
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(candidate))
    if candidate.resolve() != candidate:
        raise ConfigError("score report output must not contain symbolic links")
    if candidate.suffix.lower() != ".html":
        raise ConfigError("score report output must have an .html extension")
    if candidate.is_relative_to(root) and not candidate.is_relative_to(root / ".rung/cache/score"):
        raise ConfigError(
            "write score reports inside .rung/cache/score/ or outside the project to protect verified inputs"
        )
    if candidate.exists() and (not candidate.is_file() or score_page.MARKER not in candidate.read_text()[:100]):
        raise ConfigError("refusing to overwrite a file that is not a Rung score report")
    return candidate
