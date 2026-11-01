from __future__ import annotations

from pathlib import Path

import pytest

from rung.core import config


def test_load_and_round_trip(project: Path) -> None:
    cfg = config.load(project)
    assert cfg.name == "demo"
    assert cfg.check("unit").covers == ["src/*.py"]
    config.save(cfg)
    again = config.load(project)
    assert again.check("unit").options == cfg.check("unit").options
    assert again.llm.provider == "none"


def test_round_trip_escapes_quotes(project: Path) -> None:
    cfg = config.load(project)
    cfg.checks.append(config.Check("q", "command", [], {"run": 'echo "hi" \\ there'}))
    config.save(cfg)
    assert config.load(project).check("q").get("run") == 'echo "hi" \\ there'


def test_find_root_walks_up(project: Path) -> None:
    assert config.find_root(project / "src") == project.resolve()


@pytest.mark.parametrize(
    "body, message",
    [
        ('[[check]]\nkind = "tla"\n', "needs both"),
        ('[[check]]\nid = "x"\nkind = "magic"\n', "kind must be"),
        (
            '[[check]]\nid = "x"\nkind = "command"\n[[check]]\nid = "x"\nkind = "command"\n',
            "duplicate",
        ),
    ],
)
def test_invalid_checks_are_rejected(tmp_path: Path, body: str, message: str) -> None:
    (tmp_path / "rung.toml").write_text(body)
    with pytest.raises(config.ConfigError, match=message):
        config.load(tmp_path)


def test_missing_option_names_the_check(project: Path) -> None:
    check = config.Check("spec", "tla")
    with pytest.raises(config.ConfigError, match="'spec' \\(tla\\) needs `spec`"):
        check.require("spec")
