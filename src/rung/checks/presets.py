"""Built-in scanner presets for `kind = "scan"` checks.

Each preset is a command that writes SARIF, plus sensible defaults for which
files it covers and which severity blocks. `{out}` in a command is replaced with
a temporary file path; a command without `{out}` must write SARIF to stdout.

Commands marked `verified` were run against real output while writing Rung.
Any preset can be overridden field by field in `rung.toml`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    name: str
    purpose: str
    run: str
    install: str
    fail_on: str = "warning"
    # Severity for results the tool reports without a level (gitleaks reports none).
    default_level: str = "warning"
    # Days a verdict stays valid even if no file changes: advisory databases update daily.
    max_age_days: int | None = None
    covers: list[str] = field(default_factory=lambda: ["**/*"])
    verified: bool = False


PRESETS: dict[str, Preset] = {
    p.name: p
    for p in (
        Preset(
            "gitleaks",
            "secrets committed to the code",
            # --redact keeps secret values out of the report, the ledger and the terminal.
            "gitleaks dir . --no-banner --redact -f sarif -r {out}",
            "brew install gitleaks  |  https://github.com/gitleaks/gitleaks",
            default_level="error",
            verified=True,
        ),
        Preset(
            "osv-scanner",
            "dependencies with known vulnerabilities",
            # --no-resolve scans manifests as written. Transitive resolution fails the whole scan when a
            # manifest names a package the registry does not have; the `deps` check reports that package.
            "osv-scanner scan source --no-resolve -r . -f sarif --output-file {out}",
            "brew install osv-scanner  |  https://github.com/google/osv-scanner",
            max_age_days=1,
            covers=[
                "**/requirements*.txt",
                "**/pyproject.toml",
                "**/poetry.lock",
                "**/uv.lock",
                "**/Pipfile.lock",
                "**/package.json",
                "**/package-lock.json",
                "**/pnpm-lock.yaml",
                "**/yarn.lock",
                "**/go.mod",
                "**/go.sum",
                "**/Cargo.lock",
                "**/Gemfile.lock",
                "**/composer.lock",
                "**/pom.xml",
                "**/gradle.lockfile",
            ],
            verified=True,
        ),
        Preset(
            "zizmor",
            "insecure GitHub Actions workflows",
            "zizmor --offline --format sarif .",
            "brew install zizmor  |  https://github.com/zizmorcore/zizmor",
            covers=[".github/workflows/**", ".github/actions/**", "action.yml", "action.yaml"],
            verified=True,
        ),
        Preset(
            "semgrep",
            "insecure code patterns (injection, SSRF, unsafe deserialization)",
            "semgrep scan --config p/default --sarif --metrics off --quiet --output {out}",
            "pipx install semgrep  |  https://semgrep.dev",
        ),
        Preset(
            "opengrep",
            "insecure code patterns, with the open Semgrep fork",
            "opengrep scan --config p/default --sarif --quiet --output {out}",
            "https://github.com/opengrep/opengrep",
        ),
        Preset(
            "trivy",
            "vulnerabilities and misconfigurations in containers and infrastructure code",
            "trivy fs --scanners vuln,misconfig,secret --format sarif --output {out} .",
            "brew install trivy  |  https://trivy.dev",
        ),
    )
}
