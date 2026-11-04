"""The feature map: its contract, a person's approval, coverage of approved sub-features, and drift."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from rung import cli
from rung.analysis import kit, survey
from rung.core.config import LLMConfig, load
from rung.drafting import feature_map
from rung.drafting.providers import Provider, check_shape
from rung.engines import map_page
from tests.conftest import commit_all

INDEX = """# Shop verification map

## Baseline preconditions

- Start `python -m shop.web --port <free port>`; ready when GET / answers 200.

## Driving conventions

- Playwright; prefer ARIA roles and accessible names.

## Proof and skip reporting

- Capture the action and the resulting state. Report an unreachable path with its missing precondition.

## Features

- [Apply a coupon](coupons.md) covers valid, unknown and oversized coupons.
- [Pay for the cart](pay.md) covers paying the total.
"""
COUPONS = """# Apply a coupon

A shopper types a coupon code and the total drops by the coupon's value.

## Sub-features

- `coupon-apply` applies a valid code and lowers the total.
- `coupon-unknown` says an unknown code is unknown and leaves the total alone.

## How to get to it (user POV)

- Type in the textbox named "Coupon code" and choose "Apply".
- Open `/?coupon=WELCOME5` directly.

## Driving it with Playwright

Preconditions:

- The cart holds the two seeded items.

- **Apply.** Fill "Coupon code" with WELCOME5 and choose "Apply". The total reads $32.00.

## Gotchas

- Codes are upper-cased before lookup.
"""
PAY = COUPONS.replace("Apply a coupon", "Pay for the cart").replace("coupon-apply", "pay-card").replace(
    "coupon-unknown", "pay-receipt")  # fmt: skip


class MapProvider(Provider):
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        super().__init__(LLMConfig(provider="none"))
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, *, schema: dict[str, Any], system: str = "", **_: object) -> dict[str, Any]:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        check_shape(reply, schema)
        return reply


def _reply(**files: str) -> dict[str, Any]:
    names = {"index": "README.md", "coupons": "coupons.md", "pay": "pay.md"}
    return {
        "files": [{"path": names[k], "content": v} for k, v in files.items()],
        "sources": [{"feature": k, "sources": ["shop/web.py"]} for k in files if k != "index"],
        "notes": "The Pay button is disabled in the page.",
    }


@pytest.fixture
def shop(repo: Path) -> Path:
    (repo / "shop").mkdir()
    (repo / "shop" / "web.py").write_text("from http.server import BaseHTTPRequestHandler  # text/html\n")
    (repo / "rung.toml").write_text('[project]\nname = "shop"\n\n[llm]\nprovider = "claude-cli"\n')
    commit_all(repo, "shop")
    return repo


def test_a_map_that_follows_the_contract_parses_into_features() -> None:
    features, problems = feature_map.check_map({"README.md": INDEX, "coupons.md": COUPONS, "pay.md": PAY})
    assert problems == []
    coupons = next(f for f in features if f.slug == "coupons")
    assert [i for i, _ in coupons.subfeatures] == ["coupon-apply", "coupon-unknown"]
    assert len(coupons.entry_points) == 2 and coupons.harness == "Playwright" and coupons.gotchas


@pytest.mark.parametrize("broken, complaint", [
    (COUPONS.replace("## Gotchas", "## Notes"), "exactly these H2 sections"),
    (COUPONS.replace("- `coupon-apply` applies", "- applies"), "without a `backticked-id`"),
    (COUPONS.replace("Preconditions:", "Before:"), "must start with `Preconditions:`"),
    (COUPONS.replace("- Type in the textbox", "Type in the textbox").replace("- Open `/", "Open `/"),
     "lists no entry point"),
    (COUPONS.replace("`coupon-unknown`", "`Coupon Unknown`"), "must be kebab-case"),
])  # fmt: skip
def test_a_map_that_breaks_the_contract_is_sent_back(broken: str, complaint: str) -> None:
    _, problems = feature_map.check_map({"README.md": INDEX, "coupons.md": broken, "pay.md": PAY})
    assert any(complaint in p for p in problems), problems


def test_index_must_link_every_feature_and_ids_must_be_unique() -> None:
    _, problems = feature_map.check_map({"README.md": INDEX.replace("(pay.md)", "(other.md)"), "coupons.md": COUPONS,
                                         "pay.md": COUPONS.replace("Apply a coupon", "Pay")})  # fmt: skip
    assert any("does not link pay.md" in p for p in problems)
    assert any("'coupon-apply' is used by both" in p for p in problems)


def test_drafting_repairs_until_the_contract_holds_and_approves_nothing(shop: Path) -> None:
    provider = MapProvider([_reply(index=INDEX, coupons=COUPONS.replace("Preconditions:", "")),
                            _reply(index=INDEX, coupons=COUPONS, pay=PAY)])  # fmt: skip
    result = feature_map.draft(provider, shop, survey.survey(shop), context="ctx")
    assert result.ok and result.rounds == 2
    assert "must start with `Preconditions:`" in provider.prompts[1]
    state = feature_map.load_state(shop)
    assert state is not None and state.with_status("proposed") == ["coupons", "pay"]
    assert state.features["pay"]["sources"] == ["shop/web.py"]


def _approve(shop: Path, *slugs: str, status: str = "approved") -> feature_map.MapState:
    state = feature_map.load_state(shop)
    assert state is not None
    for slug in slugs:
        feature_map.set_status(shop, state, slug, status, by="tester")
    state.save(shop)
    return state


def test_only_approved_features_reach_the_drive_script_prompt(shop: Path) -> None:
    feature_map.draft(MapProvider([_reply(index=INDEX, coupons=COUPONS, pay=PAY)]), shop, survey.survey(shop),
                      context="ctx")  # fmt: skip
    _approve(shop, "coupons")
    _approve(shop, "pay", status="not-built")
    block, ids, sources = feature_map.approved_block(shop)
    assert "--- coupons.md ---" in block and "--- pay.md ---" not in block
    assert "not built yet; do not drive them: pay" in block
    assert ids == ["coupon-apply", "coupon-unknown"] and sources == ["shop/web.py"]


def test_every_approved_sub_feature_needs_a_scenario_or_an_unreachable_reason() -> None:
    ids = ["coupon-apply", "coupon-unknown", "pay-card"]
    result = {"scenarios": [{"name": "coupon-apply"}, {"name": "edge", "covers": ["coupon-unknown"]}],
              "unreachable": {"pay-card": "the Pay button is disabled"}}  # fmt: skip
    assert feature_map.uncovered(ids, result) == []
    result["unreachable"] = {"pay-card": "  "}  # a reason is required
    assert feature_map.uncovered(ids, result) == ["pay-card"]


def test_changing_an_approved_features_code_or_file_drifts_the_map(shop: Path) -> None:
    feature_map.draft(MapProvider([_reply(index=INDEX, coupons=COUPONS)]), shop, survey.survey(shop), context="ctx")
    state = _approve(shop, "coupons")
    assert feature_map.drifted(shop, state) == {}
    (shop / "shop" / "web.py").write_text("# the page changed\n")
    assert feature_map.drifted(shop, state) == {"coupons": ["shop/web.py"]}
    state = _approve(shop, "coupons")  # approving again pins the new code
    (shop / feature_map.MAP_DIR / "coupons.md").write_text(COUPONS + "\n- one more gotcha\n")
    assert list(feature_map.drifted(shop, state)) == ["coupons"]


def test_a_refresh_keeps_approval_for_unchanged_features_and_asks_again_for_changed_ones(shop: Path) -> None:
    info = survey.survey(shop)
    feature_map.draft(MapProvider([_reply(index=INDEX, coupons=COUPONS, pay=PAY)]), shop, info, context="ctx")
    _approve(shop, "coupons", "pay")
    changed_pay = PAY.replace("are upper-cased", "are trimmed and upper-cased")
    provider = MapProvider([_reply(index=INDEX, coupons=COUPONS, pay=changed_pay)])
    feature_map.draft(provider, shop, info, context="ctx")
    assert "--- coupons.md (status: approved) ---" in provider.prompts[0]
    state = feature_map.load_state(shop)
    assert state is not None
    assert state.features["coupons"]["status"] == "approved" and state.features["pay"]["status"] == "proposed"


def test_a_not_built_decision_outlives_a_refresh_that_leaves_the_feature_out(shop: Path) -> None:
    info = survey.survey(shop)
    feature_map.draft(MapProvider([_reply(index=INDEX, coupons=COUPONS, pay=PAY)]), shop, info, context="ctx")
    _approve(shop, "pay", status="not-built")
    feature_map.draft(MapProvider([_reply(index=INDEX.replace("- [Pay for the cart](pay.md) covers paying the "
                                                              "total.\n", ""), coupons=COUPONS)]),
                      shop, info, context="ctx")  # fmt: skip
    state = feature_map.load_state(shop)
    assert state is not None and state.features["pay"]["status"] == "not-built"
    assert not (shop / feature_map.MAP_DIR / "pay.md").exists()


def test_rung_map_approves_from_the_command_line_and_writes_the_page(shop: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("rung.drafting.providers.get",
                        lambda llm: MapProvider([_reply(index=INDEX, coupons=COUPONS, pay=PAY)]))  # fmt: skip
    assert cli.main(["-C", str(shop), "map", "--no-color"]) == 0
    text = capsys.readouterr().out
    assert "PROPOSED" in text and "coupon-apply" in text and "Next: rung map" in text
    assert cli.main(["-C", str(shop), "map", "--approve", "coupons", "--not-built", "pay", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {f["feature"]: f["status"] for f in data["features"]} == {"coupons": "approved", "pay": "not-built"}
    assert data["next"]["command"] == "rung draft runtime"
    assert (shop / data["page"]).exists()
    assert cli.main(["-C", str(shop), "map", "--approve", "nope"]) == 2


def test_draft_runtime_refuses_a_map_with_nothing_approved(shop: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("rung.drafting.providers.get",
                        lambda llm: MapProvider([_reply(index=INDEX, coupons=COUPONS)]))  # fmt: skip
    assert cli.main(["-C", str(shop), "map"]) == 0
    capsys.readouterr()
    assert cli.main(["-C", str(shop), "draft", "runtime"]) == 2
    assert "no approved features yet" in capsys.readouterr().err


def test_the_kit_shows_the_map_and_asks_for_approval_then_a_script(shop: Path) -> None:
    feature_map.draft(MapProvider([_reply(index=INDEX, coupons=COUPONS)]), shop, survey.survey(shop), context="ctx")
    built = kit.build(load(shop), survey.survey(shop))
    app = next(p for p in built.parts if p.key == "app")
    assert app.entries[0].state == "proposed" and app.state == "attention"
    assert ("rung map", "feature map: 1 feature(s) wait for a person's decision") in built.next_steps
    _approve(shop, "coupons")
    built = kit.build(load(shop), survey.survey(shop))
    assert built.next_steps[0][0] == "rung draft runtime"


def test_the_page_shows_each_sub_features_latest_result_and_screenshots(shop: Path) -> None:
    feature_map.draft(MapProvider([_reply(index=INDEX, coupons=COUPONS)]), shop, survey.survey(shop), context="ctx")
    _approve(shop, "coupons")
    run = shop / ".rung" / "evidence" / "runtime" / "20261104T000000Z-aaaa" / "seed-1"
    (run / "coupon-apply").mkdir(parents=True)
    (run / "coupon-apply" / "01-total-32.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "result.json").write_text(json.dumps({"seed": 1, "scenarios": [
        {"name": "coupon-apply", "category": "happy", "passed": True, "assertions": []},
        {"name": "edge", "category": "input", "covers": ["coupon-unknown"], "passed": False,
         "assertions": [{"name": "says unknown", "passed": False}]}]}))  # fmt: skip
    page = map_page.write(shop, check_ids=["runtime"]).read_text()
    assert "APPROVED" in page and "PROVEN" in page and "01-total-32.png" in page
    assert "FAILED" in page and "says unknown" in page


def test_init_drafts_the_map_and_waits_for_approval_under_yes(shop: Path, monkeypatch) -> None:
    from rung.cli.commands import setup
    from rung.cli.output import Output

    monkeypatch.setattr("rung.drafting.providers.get",
                        lambda llm: MapProvider([_reply(index=INDEX, coupons=COUPONS)]))  # fmt: skip
    monkeypatch.setattr("rung.cli.commands.draft.cmd_draft", lambda ns, out: pytest.fail("no script before approval"))
    monkeypatch.setattr(setup, "_has", lambda probe: False)
    args = argparse.Namespace(yes=True, no_drafts=False)
    info = survey.survey(shop)
    info.model_candidates = []
    done = setup._draft_kit(args, shop, info, load(shop), True, Output())
    assert done[0]["kind"] == "runtime" and "approve features with `rung map`" in done[0]["note"]


def test_the_model_is_shown_the_files_that_serve_the_app_and_what_they_import(shop: Path) -> None:
    from rung.drafting import runtime

    (shop / "shop" / "pricing.py").write_text("def total(items, coupon):\n    return sum(items) - coupon\n")
    (shop / "shop" / "web.py").write_text(
        "from http.server import BaseHTTPRequestHandler\nfrom shop.pricing import total\n# text/html\n"
    )
    commit_all(shop, "pricing")
    info = survey.survey(shop)
    assert runtime.app_focus(shop, info) == ["shop/web.py", "shop/pricing.py"]
    assert "--- shop/web.py ---" in runtime.gather_context(shop, info, runtime.app_focus(shop, info))


def test_the_page_renders_inline_markdown_but_never_raw_html() -> None:
    assert map_page._inline("type `<script>` and **save**") == "type <code>&lt;script&gt;</code> and <b>save</b>"
    assert map_page._block("Intro\n- one\n- `two`\nEnd") == (
        "<p>Intro</p><ul><li>one</li><li><code>two</code></li></ul><p>End</p>"
    )


def test_app_focus_follows_python_and_typescript_imports_into_sub_projects(repo: Path) -> None:
    from rung.drafting import runtime

    files = {
        "backend/pyproject.toml": '[project]\nname = "b"\ndependencies = ["fastapi"]\n',
        "backend/app/__init__.py": "",
        "backend/app/routes.py": "from fastapi import APIRouter\nfrom app import pricing, carts\nrouter = APIRouter()\n"
        "@router.get('/api/cart')\ndef cart():\n    return carts.load()\n",
        "backend/app/pricing.py": "def total(items, coupon):\n    return sum(items) - coupon  # the pricing rules\n",
        "backend/app/carts.py": "from app.pricing import total\ndef load():\n    return total([1, 2], 0)  # the cart\n",
        "frontend/package.json": '{"dependencies": {"next": "16"}}',
        "frontend/tsconfig.json": "{}",
        "frontend/app/cart/page.tsx": "import { CartView } from '@/components/cart-view'\n"
        "import { Button } from '@/components/ui/button'\nexport default function Page() { return <CartView /> }\n",
        "frontend/components/cart-view.tsx": "import { CouponForm } from './coupon-form'\n"
        "export function CartView() { return <CouponForm /> }\n",
        "frontend/components/coupon-form.tsx": "export function CouponForm() { return <form aria-label='Coupon' /> }\n",
        "frontend/components/ui/button.tsx": "export function Button() { return <button /> }  // a primitive\n",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    commit_all(repo, "app")
    focus = runtime.app_focus(repo, survey.survey(repo))
    assert {"backend/app/routes.py", "backend/app/pricing.py", "backend/app/carts.py", "frontend/app/cart/page.tsx",
            "frontend/components/cart-view.tsx", "frontend/components/coupon-form.tsx"} <= set(focus)  # fmt: skip
    assert "frontend/components/ui/button.tsx" not in focus and "backend/app/__init__.py" not in focus
