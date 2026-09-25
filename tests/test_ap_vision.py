from pathlib import Path
from dataclasses import replace
import pytest
from ba_automator.ap_vision import APVision, classify_ap
from ba_automator.vision import StartupVision, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def vision():
    return APVision(StartupVision())


@pytest.mark.parametrize(
    "name,kind",
    [
        ("credits-upper", "commission_list"),
        ("reports-list", "commission_list"),
        ("reports-detail", "detail"),
        ("reports-quantity-two", "detail"),
        ("credit-detail", "detail"),
        ("credit-start", "confirm"),
        ("credit-receipt", "receipt"),
        ("hard-receipt", "receipt"),
        ("commission-chooser", "commissions"),
        ("campaign-stable", "campaign"),
        ("missions", "normal"),
        ("hard13", "hard_list"),
        ("hard13-open", "hard_list"),
        ("hard-detail", "detail"),
        ("hard-available", "detail"),
        ("hard-split-title", "detail"),
        ("hard-small-index", "detail"),
        ("survey-area4", "hard_list"),
        ("credits-help-icon", "commission_list"),
    ],
)
def test_real_sanitized_sweep_screens(vision, name, kind):
    result = vision.analyze((FIXTURES / f"ap-{name}.png").read_bytes())
    assert result.kind == kind
    if name == "credits-help-icon":
        assert [s.id for s in result.stages] == list("IJKLM") and all(
            s.target is None for s in result.stages
        )
    if name == "credits-upper":
        assert [(s.id, s.stars) for s in result.stages] == [
            ("D", 3),
            ("E", 3),
            ("F", 3),
            ("G", 3),
            ("H", 0),
        ]
    if name == "reports-list":
        assert result.stages[0].id == "J" and result.stages[0].stars == 3
    if name == "reports-detail":
        assert (
            result.strategy,
            result.stage,
            result.cost,
            result.stars,
            result.after,
        ) == ("reports", "J", 40, 3, 386)
    if name == "credit-detail":
        assert (result.strategy, result.stage, result.cost, result.stars) == (
            "credits",
            "G",
            35,
            3,
        )
    if name == "hard-available":
        assert (
            result.stage,
            result.remaining,
            result.count,
            result.cost,
            result.after,
        ) == ("8-3", 1, 1, 20, 341)
    if name == "hard-split-title":
        assert (result.stage, result.remaining, result.cost) == ("8-1", 1, 20)
    if name == "hard-small-index":
        assert (result.stage, result.remaining, result.cost) == ("6-1", 3, 20)
    if name == "reports-quantity-two":
        assert (
            result.count == 2 and result.cost == 40 and result.after == result.ap - 80
        )
    if name == "hard-detail":
        assert result.remaining == 0 and result.target is None
    if name == "hard13":
        assert not result.right and all(s.target is None for s in result.stages)
    if name == "survey-area4":
        assert {s.id for s in result.stages} == {"4-1", "4-2", "4-3"}


def test_unknown_ap_and_wrong_projection_cannot_authorize_commission(vision):
    f = decode_frame((FIXTURES / "ap-credit-detail.png").read_bytes())
    words = vision.startup.read(f)
    missing = [w for w in words if w.text != "461/218"]
    assert classify_ap(f, missing).kind == "unknown"
    wrong = [
        replace(w, text="462→427") if w.text == "461→426" and w.center[1] < 400 else w
        for w in words
    ]
    assert classify_ap(f, wrong).kind == "unknown"


def test_dimmed_stage_lists_are_rejected(vision):
    for name in ("credits-upper", "hard13-open"):
        f = decode_frame((FIXTURES / f"ap-{name}.png").read_bytes())
        words = vision.startup.read(f)
        assert classify_ap((f * 0.5).astype("uint8"), words).kind == "unknown"


def test_missing_star_does_not_become_three_stars(vision):
    f = decode_frame((FIXTURES / "ap-credit-detail.png").read_bytes())
    words = vision.startup.read(f)
    f[428:443, 157:174] = 180
    assert classify_ap(f, words).stars == 2


def test_hard_area_total_must_match_individual_stars(vision):
    f = decode_frame((FIXTURES / "ap-hard13-open.png").read_bytes())
    words = vision.startup.read(f)
    changed = [
        replace(w, text="Star Acquisition (8/9)") if "Star Acquisition" in w.text else w
        for w in words
    ]
    assert classify_ap(f, changed).kind == "unknown"


def test_zero_affordable_quantity_is_recognized_without_a_spend_target(vision):
    f = decode_frame((FIXTURES / "ap-credit-detail.png").read_bytes())
    words = vision.startup.read(f)
    changed = []
    for w in words:
        if 493 <= w.center[0] <= 620 and w.center[1] < 48:
            w = replace(w, text="10/218")
        elif 904 <= w.center[0] <= 970 and 277 <= w.center[1] <= 330:
            w = replace(w, text="0")
        elif 965 <= w.center[0] <= 1115 and 338 <= w.center[1] <= 382 and "→" in w.text:
            w = replace(w, text="10→10")
        changed.append(w)
    result = classify_ap(f, changed)
    assert (
        result.kind == "detail"
        and result.count == 0
        and result.cost is None
        and result.target is None
    )


def test_commission_index_and_title_must_agree(vision):
    f = decode_frame((FIXTURES / "ap-credits-upper.png").read_bytes())
    words = vision.startup.read(f)
    changed = [replace(w, text="08") if w.text == "07" else w for w in words]
    result = classify_ap(f, changed)
    assert all(
        s.id != "G" and not (s.id == "H" and s.stars == 3) for s in result.stages
    )


def test_commission_detail_requires_matching_name_and_number(vision):
    f = decode_frame((FIXTURES / "ap-credit-detail.png").read_bytes())
    words = vision.startup.read(f)
    changed = [
        (
            replace(w, text="08")
            if 125 <= w.center[0] <= 195 and 185 <= w.center[1] <= 240
            else w
        )
        for w in words
    ]
    assert classify_ap(f, changed).kind == "unknown"


def test_hard_projected_and_current_attempts_must_agree(vision):
    f = decode_frame((FIXTURES / "ap-hard-available.png").read_bytes())
    words = vision.startup.read(f)
    changed = [
        (
            replace(w, text=w.text.replace("Remaining: 0/3", "Remaining: 1/3"))
            if 372 < w.center[1] < 416
            else w
        )
        for w in words
    ]
    assert classify_ap(f, changed).kind == "unknown"


def test_natural_regeneration_allows_postcondition_but_not_another_spend(vision):
    result = vision.analyze((FIXTURES / "ap-regenerated-detail.png").read_bytes())
    assert result.kind == "detail" and result.stage == "4-2"
    assert (result.ap, result.after, result.remaining) == (105, 84, 1)
    assert result.target is None
