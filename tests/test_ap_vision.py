from pathlib import Path
from dataclasses import replace
import pytest
from ba_automator.ap_vision import APVision, ap_value, classify_ap
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


@pytest.mark.parametrize(
    "name,kind,ap,capacity",
    [
        ("level-up-before", "detail", 156, 218),
        ("level-up-receipt", "receipt", 356, 220),
        ("credit-start", "confirm", 461, 218),
    ],
)
def test_ap_capacity_retains_observed_level_up_refill(vision, name, kind, ap, capacity):
    screen = vision.analyze((FIXTURES / f"ap-{name}.png").read_bytes())
    assert (screen.kind, screen.ap, screen.ap_capacity) == (kind, ap, capacity)
    if name == "level-up-before":
        assert (screen.stage, screen.count, screen.cost, screen.after, screen.remaining) == (
            "11-1", 1, 20, 136, 3,
        )
    if name == "level-up-receipt":
        assert screen.count == 1


@pytest.mark.parametrize("change", ["missing", "duplicate", "malformed"])
def test_ap_and_capacity_require_the_same_unique_fraction(vision, change):
    f = decode_frame((FIXTURES / "ap-level-up-receipt.png").read_bytes())
    words = vision.startup.read(f)
    balance = [w for w in words if w.text == "356/220"]
    assert len(balance) == 1
    if change == "missing":
        words.remove(balance[0])
    elif change == "duplicate":
        words.append(replace(balance[0], text="356/218"))
    else:
        words = [replace(w, text="356/?") if w == balance[0] else w for w in words]
    screen = classify_ap(f, words)
    assert screen.kind == "receipt"
    assert screen.ap is None and screen.ap_capacity is None
    assert ap_value(words) is None


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


def test_native_hard_projection_overlap_requires_exact_crop_agreement(vision):
    from ba_automator.vision import read_game_words

    png = (FIXTURES / 'ap-hard-native-overlap.png').read_bytes()
    frame = decode_frame(png)
    # Full native OCR overlaps the final digit of 102 with the arrow label.
    assert classify_ap(frame, read_game_words(png, vision.startup)).kind == 'unknown'
    screen = vision.analyze(png)
    assert (screen.kind, screen.stage, screen.ap, screen.remaining,
            screen.count, screen.cost, screen.after) == (
        'detail', '4-3', 102, 1, 1, 20, 82,
    )


@pytest.mark.parametrize('change', ['disagree', 'low_confidence', 'malformed'])
def test_hard_projection_crop_never_infers_or_corrects_a_number(change):
    import numpy as np
    from ba_automator.ap_vision import reread_hard_projection
    from ba_automator.vision import Word

    class CropVision:
        calls = 0

        def read(self, image):
            self.calls += 1
            text, confidence = '102→82 Remaining: 0/3', .99
            if self.calls == 2:
                if change == 'disagree':
                    text = '102→32 Remaining: 0/3'
                elif change == 'low_confidence':
                    confidence = .94
                else:
                    text = '102 2→82 Remaining: 0/3'
            return [Word(text, confidence, (25, 25, 250, 55))]

    assert reread_hard_projection(np.zeros((1440, 2560, 3), dtype='uint8'), CropVision()) is None


def test_hard_projection_accepts_agreeing_1440p_reads_with_one_marginal_confidence():
    import numpy as np
    from ba_automator.ap_vision import reread_hard_projection
    from ba_automator.vision import Word

    class CropVision:
        calls = 0

        def read(self, image):
            self.calls += 1
            confidence = .946 if self.calls == 1 else .999
            return [Word('101→81', confidence, (25, 25, 135, 55)),
                    Word('Remaining: 1/3', .999, (140, 25, 250, 55))]

    result = reread_hard_projection(np.zeros((1440, 2560, 3), dtype='uint8'), CropVision())
    assert result is not None and result.text == '101→81 Remaining: 1/3'


def test_native_hard_attempt_row_baseline_jitter(vision):
    result = vision.analyze((FIXTURES / 'ap-hard-attempt-baseline.png').read_bytes())
    assert result.kind == 'detail'
    assert (result.stage, result.ap, result.count, result.cost) == ('7-1', 199, 1, 20)
    assert (result.after, result.remaining, result.stars) == (179, 3, 3)
    assert result.target == (937, 437)
