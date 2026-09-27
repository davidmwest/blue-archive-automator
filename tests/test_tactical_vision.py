"""Evidence-based ladder recognition; unavailable numbers are never inferred."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_vision import (
    ObservedOpponent, TacticalBattleVision, _cooldown, _level_crops, _read_level,
    same_opponent, same_opponent_identity,
)
from ba_automator.vision import StartupVision, Word, decode_native_frame

FIXTURES = Path(__file__).parent / 'fixtures'
OPPONENT_LEVELS = {400: (90, 90, 90), 471: (90, 90, 67), 540: (89, 83, 77)}


@pytest.fixture(scope='module')
def vision():
    return TacticalBattleVision(StartupVision())


def fixture(name):
    prefix = FIXTURES / f'tactical-battles-{name}'
    frame = cv2.imread(str(prefix.with_suffix('.png')))
    words = tuple(Word(row['text'], row['confidence'], tuple(row['box']))
                  for row in json.loads(prefix.with_suffix('.json').read_text()))
    return frame, words


@pytest.fixture
def recorded_menu_vision(vision, monkeypatch):
    """Replay actual crop reads with exact pixels, independent of CPU OCR drift."""
    frame, _ = fixture('opponents')
    metadata = json.loads((FIXTURES / 'tactical-battles-opponents-levels.json').read_text(
        encoding='utf-8'))
    readings = {}
    for label in metadata['labels']:
        crops = _level_crops(frame, *label['origin'])
        assert len(crops) == len(label['reads'])
        for crop, reading in zip(crops, label['reads']):
            readings[(crop.shape, crop.tobytes())] = reading

    def recognize(request):
        values = [readings.get((crop.shape, crop.tobytes()), ('', 0.))
                  for crop in request.img]
        return SimpleNamespace(txts=[value[0] for value in values],
                               scores=[value[1] for value in values])

    monkeypatch.setattr(vision.startup.ocr, 'text_rec', recognize)
    return TacticalBattleVision(vision.startup)


def assert_observed_menu_levels(result):
    observed = {opponent.choice.rank: opponent.choice.visible_levels
                for opponent in result.opponents}
    # The last row produces ambiguous crop reads on some CPU runtimes. An
    # omission is safe; every accepted level still has to match actual pixels.
    assert {400, 471} <= observed.keys()
    assert observed.items() <= OPPONENT_LEVELS.items()


def test_delayed_entry_trace_has_real_campaign_then_opponent_detail(vision):
    campaign = vision.classify(*fixture('delayed-entry-campaign'))
    assert (campaign.kind, campaign.target) == ('campaign', (868, 581))
    # The next saved frame was already an opponent modal. Campaign had stayed
    # visible after the first accepted input; repeating its coordinate opened
    # this modal. It must never authorize another Campaign navigation input.
    image, words = fixture('delayed-entry-opponent')
    assert any(w.normalized == 'battle opponent' for w in words)
    assert any(w.normalized == 'attack formation' for w in words)
    detail = vision.classify(image, words)
    # Uncertain unit-level OCR permits closing the proven preview, but never
    # authorizes formation entry or another Campaign navigation input.
    assert detail.kind == 'opponent'
    assert detail.opponents == ()
    assert detail.target is None


def test_recorded_menu_reads_all_visible_levels_and_hidden_cards(recorded_menu_vision):
    result = recorded_menu_vision.classify(*fixture('opponents'))
    assert result.kind == 'tactical'
    assert (result.rank, result.tickets, result.cooldown) == (571, 5, 0)
    assert [p.choice.visible_levels for p in result.opponents] == [
        (90, 90, 90), (90, 90, 67), (89, 83, 77)]
    assert [p.choice.rank for p in result.opponents] == [400, 471, 540]
    assert result.sampled_ranks == (400, 471, 540)
    assert result.refresh_seconds == 119


def test_local_ocr_menu_accepts_only_correct_visible_levels(vision):
    result = vision.classify(*fixture('opponents'))
    assert (result.kind, result.rank, result.tickets, result.cooldown) == (
        'tactical', 571, 5, 0)
    assert result.sampled_ranks == (400, 471, 540)
    assert result.refresh_seconds == 119
    assert_observed_menu_levels(result)


def test_repeated_level_pixels_avoid_ocr_but_changed_pixels_and_ceiling_do_not(recorded_menu_vision, monkeypatch):
    vision = recorded_menu_vision
    image, words = fixture('opponents')
    vision._level_cache.clear()
    batches = []
    original = vision.startup.ocr.text_rec

    def recognize(request):
        batches.append(len(request.img))
        return original(request)

    monkeypatch.setattr(vision.startup.ocr, 'text_rec', recognize)
    first = vision.classify(image, words)
    assert len(first.opponents) == 3
    assert batches == [9 * 16]
    assert vision.classify(image.copy(), words) == first
    assert batches == [9 * 16]
    assert len(vision._level_cache) <= 18

    # Metadata must still come from this capture, not from the level cache.
    changed_words = tuple(replace(w, text='Rank 399') if w.text == 'Rank 400' else w
                          for w in words)
    changed = vision.classify(image, changed_words)
    assert changed.opponents[0].choice.rank == 399
    assert batches == [9 * 16]

    altered = image.copy()
    altered[208:212, 759:763] = 0
    vision.classify(altered, words)
    assert batches[-1] == 16
    assert len(batches) == 2

    # Identical glyphs under a changed account level require fresh recognition
    # and cannot retain a level higher than the newly observed ceiling.
    limited_words = tuple(replace(w, text=w.text.replace('Lv.90', 'Lv.80'))
                          if w.text.startswith('Lv.90') else w for w in words)
    limited = vision.classify(image, limited_words)
    assert limited_words != words
    assert len(batches) == 3
    assert limited.opponents == ()


def test_level_cache_keys_native_pixels_and_discards_previous_view(vision, monkeypatch):
    vision = TacticalBattleVision(vision.startup)
    image, words = fixture('opponents')
    native = cv2.resize(image, (2560, 1440), interpolation=cv2.INTER_NEAREST)
    vision._level_cache.clear()
    batches = []
    observed_level = 42

    def recognize(request):
        batches.append(len(request.img))
        return SimpleNamespace(
            txts=[f'Lv.{observed_level}' if index % 16 in (0, 1, 8)
                  else str(observed_level) for index in range(len(request.img))],
            scores=[.99] * len(request.img))

    monkeypatch.setattr(vision.startup.ocr, 'text_rec', recognize)
    first = vision._opponents(image, words, native_frame=native)
    assert len(first) == 3
    assert all(p.choice.visible_levels == (42, 42, 42) for p in first)
    assert batches == [9 * 16]
    previous_keys = set(vision._level_cache)
    assert vision._opponents(image, words, native_frame=native.copy()) == first
    assert batches == [9 * 16]

    # The canonical image is deliberately unchanged. A cache based only on
    # its 720p pixels would reuse the old level despite new native evidence.
    native[416:424, 1518:1526] = 0
    observed_level = 41
    changed = vision._opponents(image, words, native_frame=native)
    assert changed[0].choice.visible_levels == (41, 42, 42)
    assert batches == [9 * 16, 16]
    current_keys = set(vision._level_cache)
    assert len(previous_keys - current_keys) == 1
    assert len(current_keys - previous_keys) == 1
    assert len(current_keys) <= 9

    # Lowering an account ceiling rejects the previously valid units rather
    # than retaining either cached interpretation of those same native pixels.
    limited_words = tuple(replace(w, text=w.text.replace('Lv.90', 'Lv.40'))
                          if w.text.startswith('Lv.90') else w for w in words)
    limited = vision._opponents(image, limited_words, native_frame=native)
    assert batches == [9 * 16, 16, 9 * 16]
    assert limited == ()
    assert all(key[0] == 40 and value is None
               for key, value in vision._level_cache.items())
    assert len(vision._level_cache) <= 9


def test_detail_checks_projected_ticket_and_preserves_opponent_identity(vision):
    menu = vision.classify(*fixture('opponents'))
    detail = vision.classify(*fixture('opponent-detail'))
    assert detail.kind == 'opponent'
    assert (detail.rank, detail.tickets, detail.after_tickets) == (571, 5, 4)
    assert same_opponent(menu.opponents[0], detail.opponents[0])
    assert not same_opponent(menu.opponents[1], detail.opponents[0])
    changed = replace(menu.opponents[0], choice=replace(menu.opponents[0].choice, rank=333))
    assert same_opponent(changed, detail.opponents[0])
    assert not same_opponent(changed, replace(detail.opponents[0], signature='00' * 576))


def test_ambiguous_detail_levels_allow_backout_but_never_formation_entry(vision):
    frame, words = fixture('ambiguous-preview')
    result = vision.classify(frame, words)
    assert (result.kind, result.rank, result.tickets, result.after_tickets) == (
        'opponent', 541, 2, 1)
    # The real first 79 lacks independent strong reads; the real 77 has a
    # conflicting clipped 7. Preserve those strict exclusions. A known dialog
    # can be closed and the search restarted without spending a ticket.
    assert result.opponents == () and result.target is None
    assert result.refresh_target is None
    result = vision.analyze((FIXTURES / 'tactical-battles-ambiguous-preview.png').read_bytes())
    assert result.kind == 'opponent' and result.opponents == () and result.target is None


def test_preview_backout_requires_complete_layout_rank_and_ticket_projection(vision):
    frame, words = fixture('ambiguous-preview')
    for label in ('Battle Opponent', 'Opponent Info', 'My Info', 'Attack Formation',
                  'Rank 541', '2→1'):
        changed = tuple(w for w in words if w.text != label)
        assert vision.classify(frame, changed).kind == 'unknown'
    for replacement in ('2→2', '2→0', '1→2', '2/1'):
        changed = tuple(replace(w, text=replacement) if w.text == '2→1' else w for w in words)
        assert vision.classify(frame, changed).kind == 'unknown'
    for label in ('Rank 541', '2→1'):
        changed = tuple(replace(w, confidence=.89) if w.text == label else w for w in words)
        assert vision.classify(frame, changed).kind == 'unknown'
    frame[543:601, 528:751] = 0
    assert vision.classify(frame, words).kind == 'unknown'


def test_history_identity_survives_level_up_but_entry_still_requires_current_level():
    signature = np.random.default_rng(7).integers(0, 256, 576, dtype=np.uint8).tobytes().hex()
    before = ObservedOpponent(Opponent('before', 120, 79, (79, 79, 79)),
                              'Ｓｃｏｕｔ', (830, 250), signature)
    after = replace(before, name=' scout ', choice=Opponent('after', 100, 80, (80, 80, 80)))
    assert same_opponent_identity(before, after)
    assert not same_opponent(before, after)
    same_level = replace(after, choice=replace(after.choice, level=79, visible_levels=(79, 79, 79)))
    assert same_opponent(before, same_level)
    assert not same_opponent_identity(before, replace(after, name='Another Scout'))
    assert not same_opponent_identity(replace(before, name=''), replace(after, name=''))
    for invalid in ('00' * 576, 'not hex', '01' * 575, None):
        assert not same_opponent_identity(before, replace(after, signature=invalid))
    different = np.random.default_rng(8).integers(0, 256, 576, dtype=np.uint8).tobytes().hex()
    assert not same_opponent_identity(before, replace(after, signature=different))


def test_visible_unreadable_level_omits_candidate_instead_of_assuming_hidden(vision):
    frame, words = fixture('opponents')
    before = vision.classify(frame, words)
    frame[205:230, 740:782] = 0
    result = vision.classify(frame, words)
    assert result.kind == 'tactical' and result.tickets == 5
    assert 400 not in [op.choice.rank for op in result.opponents]
    assert result.opponents == tuple(op for op in before.opponents if op.choice.rank != 400)
    assert any(op.choice.rank == 471 for op in result.opponents)


def test_hidden_card_must_be_observed_not_inferred_from_missing_ocr(vision):
    frame, words = fixture('opponents')
    frame[209:254, 810:863] = 0
    result = vision.classify(frame, words)
    assert result.kind == 'tactical'
    assert 400 not in [op.choice.rank for op in result.opponents]


def test_unreadable_opponents_leave_menu_usable_for_next_refresh(vision):
    result = vision.classify(*fixture('menu-ready'))
    assert result.kind == 'tactical' and result.tickets == 5
    assert result.refresh_target == (1174, 147)


def test_loading_overlay_blocks_the_still_readable_old_menu(vision):
    frame, words = fixture('refresh-loading')
    result = vision.classify(frame, words)
    assert result.kind == 'unknown'
    assert result.target is None and result.refresh_target is None
    assert result.opponents == () and result.sampled_ranks == ()
    assert result.tickets is None and result.refresh_seconds is None
    # This is the actual failure: the old timer and controls remain perfectly
    # readable while the refresh request is pending. They must not be used.
    without_loading = tuple(w for w in words if w.text != 'NowLoading')
    underlying = vision.classify(frame, without_loading)
    assert (underlying.kind, underlying.tickets, underlying.refresh_seconds) == (
        'tactical', 2, 105)
    assert underlying.refresh_target == (1174, 147)
    assert vision.analyze(
        (FIXTURES / 'tactical-battles-refresh-loading.png').read_bytes()).kind == 'unknown'


@pytest.mark.parametrize('text', ['NowLoading', 'Now Loading', 'Now Loading...',
                                 'Now Loading …', 'NOW LOADING'])
def test_loading_guard_accepts_spacing_and_ellipsis_variants(vision, text):
    frame, words = fixture('refresh-loading')
    words = tuple(replace(w, text=text) if w.text == 'NowLoading' else w for w in words)
    assert vision.classify(frame, words).kind == 'unknown'


def test_loading_guard_does_not_match_unrelated_names_or_labels(vision):
    frame, words = fixture('refresh-loading')
    original = next(w for w in words if w.text == 'NowLoading')
    others = tuple(w for w in words if w is not original)
    for replacement in (
            replace(original, box=(470, 293, 670, 320)),
            replace(original, text='Loading'),
            replace(original, text='Now Loading Complete')):
        assert vision.classify(frame, others + (replacement,)).kind == 'tactical'


def test_menu_authorization_returns_after_a_completed_refresh(vision):
    # Independent real captures: a pending request must yield no controls;
    # the observed settled post-refresh menu supplies its new countdown.
    pending = vision.classify(*fixture('refresh-loading'))
    completed = vision.classify(*fixture('refresh-121'))
    assert pending.kind == 'unknown' and pending.refresh_target is None
    assert completed.kind == 'tactical' and completed.refresh_seconds == 121
    assert completed.refresh_target == (1174, 147)


def test_observed_post_battle_ticket_label_without_space_still_recognizes_menu(vision):
    result = vision.classify(*fixture('menu-after-defeat'))
    assert (result.kind, result.rank, result.tickets, result.cooldown) == ('tactical', 571, 4, 0)
    assert result.refresh_seconds == 62
    assert result.sampled_ranks == (428, 488, 536)


@pytest.mark.parametrize('name,checked,seconds', [
    ('formation-settled', False, 95), ('skip-settled', True, 92)])
def test_both_observed_skip_states_and_formation_deadline(vision, name, checked, seconds):
    result = vision.classify(*fixture(name))
    assert result.kind == 'formation'
    assert result.skip_selected is checked
    assert result.formation_seconds == seconds
    assert result.target == (1168, 669)


@pytest.mark.parametrize('name,target', [
    ('back-timeout', (640, 505)), ('before-scout-back', (765, 504))])
def test_expiry_notices_are_not_mistaken_for_underlying_formation(vision, name, target):
    result = vision.classify(*fixture(name))
    assert (result.kind, result.target) == ('timeout_notice', target)


def test_dimmed_menu_and_missing_ticket_remain_unknown(vision):
    frame, words = fixture('opponents')
    assert vision.classify((frame * .5).astype('uint8'), words).kind == 'unknown'
    words = tuple(w for w in words if not w.text.startswith('Tickets Owned'))
    assert vision.classify(frame, words).kind == 'unknown'


def test_level_ocr_requires_evidence_and_rejects_conflicts():
    assert _read_level([('Lv77', .88), ('Lv7', .79), ('π', .99)], 90) == 77
    assert _read_level([('Lv77', .88), ('Lv7', .8), ('π', .99)], 90) == 77
    assert _read_level([('Lv77', .8), ('Lv7', .8), ('π', .99)], 90) is None
    assert _read_level([('Lv77', .95), ('Lv71', .95)], 90) is None
    assert _read_level([('Lv91', .95)], 90) is None
    correlated_error = [('Lv30', .92), ('Lv30', .94)] + [('30', .99)] * 7
    assert _read_level(correlated_error + [('80', .98)], 90) is None


def test_clipped_eight_cannot_make_level_eighty_opponent_look_like_thirty(vision):
    # The actual opponent has 80/80/80. Tight OCR reads the last student's
    # 8 as 3 even with high confidence. Wider contradictory evidence excludes
    # this candidate rather than selecting it as an artificially weak team.
    result = vision.classify(*fixture('clipped-eight'))
    assert result.kind == 'tactical'
    assert result.sampled_ranks[0] == 431
    assert 431 not in [op.choice.rank for op in result.opponents]


def test_clipped_seven_cannot_make_level_seventy_five_look_like_fifteen(vision):
    # Full labels read 75 at 0.80/0.84; digits alone incorrectly read 15.
    # Contradictory lower-confidence labels veto the apparent easy opponent.
    result = vision.classify(*fixture('clipped-seven'))
    assert result.kind == 'tactical'
    assert result.sampled_ranks[0] == 429
    assert 429 not in [op.choice.rank for op in result.opponents]
    values = [('Lv.75', .838), ('Lv.75', .80)] + [('15', .99)] * 7
    assert _read_level(values + [('15', .99), ('15', .99)], 90) is None


def test_portrait_background_cannot_discount_level_eighty_eight_to_thirty_eight(vision):
    # The list's 14–16px crops read 38. One uncertain 88 guard was insufficient
    # to veto it. More vertical context preserves confident contradictory 88s;
    # the opponent must be omitted, never assigned either guessed score.
    from rapidocr.ch_ppocr_rec.typings import TextRecInput

    frame, words = fixture('clipped-eighty-eight')
    result = vision.classify(frame, words)
    assert (result.kind, result.rank, result.tickets) == ('tactical', 541, 4)
    assert result.sampled_ranks == (394, 481, 494)
    assert 494 not in [op.choice.rank for op in result.opponents]
    crops = _level_crops(frame, 1070, 524)
    readings = vision.startup.ocr.text_rec(TextRecInput(crops))
    values = list(zip(readings.txts, readings.scores))
    assert any(text == '88' and confidence >= .94 for text, confidence in values[13:])
    assert _read_level(values, 88) is None

    detail = vision.classify(*fixture('clipped-eighty-eight-detail'))
    assert (detail.kind, detail.rank, detail.tickets, detail.after_tickets) == ('opponent', 541, 4, 3)
    assert detail.opponents[0].choice.visible_levels == (86, 88, 88)


def test_recorded_portrait_reads_reject_eighty_eight_discounted_to_thirty_eight():
    # Retain the original unsafe interpretation as a deterministic regression.
    # A different CPU may conservatively reject it even before the taller reads.
    path = FIXTURES / 'tactical-battles-clipped-eighty-eight-levels.json'
    values = json.loads(path.read_text(encoding='utf-8'))['reads']
    assert _read_level(values[:13], 88) == 38
    assert _read_level(values, 88) is None


@pytest.mark.parametrize('level', [1, 8, 30, 38, 67, 73, 88, 90])
def test_taller_guards_do_not_coerce_or_forbid_verified_low_levels(level):
    readings = [(f'Lv.{level}', .98)] * 2 + [(str(level), .99)] * 6
    readings += [(f'Lv.{level}', .98)] + [(str(level), .99)] * 7
    assert _read_level(readings, 90) == level


def test_lower_level_opponent_detail_uses_enlarged_digits(vision):
    result = vision.classify(*fixture('skip-opponent'))
    assert result.kind == 'opponent'
    assert result.opponents[0].choice.visible_levels == (73, 74, 73)
    assert result.opponents[0].choice.level == 76


def test_entire_local_ocr_path_handles_outline_fonts_and_help_icon(vision):
    for name, kind in [('opponents', 'tactical'), ('formation-settled', 'formation')]:
        result = vision.analyze((FIXTURES / f'tactical-battles-{name}.png').read_bytes())
        assert result.kind == kind
        if kind == 'tactical':
            assert result.all_ahead
            assert_observed_menu_levels(result)


def test_all_ahead_requires_three_distinct_verified_ranks(vision):
    frame, words = fixture('opponents')
    assert vision.classify(frame, words).all_ahead
    changed = tuple(replace(w, text='Rank 600') if w.text == 'Rank 540' else w for w in words)
    result = vision.classify(frame, changed)
    assert not result.all_ahead and result.sampled_ranks == (400, 471, 600)
    changed = tuple(replace(w, text='Rank 400') if w.text == 'Rank 540' else w for w in words)
    result = vision.classify(frame, changed)
    assert not result.all_ahead and result.sampled_ranks == ()
    changed = tuple(w for w in words if w.text != 'Rank 540')
    result = vision.classify(frame, changed)
    assert not result.all_ahead and result.sampled_ranks == ()


def test_cooldown_requires_observed_clock_and_rejects_invalid_seconds():
    label = Word('Standby Time', .98, (57, 515, 178, 541))
    assert _cooldown((label,)) is None
    assert _cooldown((label, Word('01:52', .99, (180, 518, 240, 538)))) == 112
    assert _cooldown((label, Word('01:72', .99, (180, 518, 240, 538)))) is None
    assert _cooldown((label, Word('e -:--', .75, (166, 518, 214, 538)))) == 0
    assert _cooldown((label, Word('e 01:52', .75, (166, 518, 240, 538)))) is None
    assert _cooldown((Word('e -:--', .75, (166, 518, 214, 538)),)) is None


def test_native_missing_clock_is_read_from_labeled_crop(vision, monkeypatch):
    prefix = FIXTURES / 'tactical-battles-native-missing-clock'
    png = prefix.with_suffix('.png').read_bytes()
    words = tuple(Word(row['text'], row['confidence'], tuple(row['box']))
                  for row in json.loads(prefix.with_suffix('.json').read_text()))
    assert _cooldown(words) is None
    original_read = vision.startup.read
    crops = []

    def read(image):
        if image.shape[:2] == (1440, 2560):
            # Replay canonical observations at native coordinates. Only the
            # unchanged, sanitized clock strip is read with actual OCR below.
            return [replace(w, box=tuple(value * 2 for value in w.box)) for w in words]
        crops.append(image.shape[:2])
        return original_read(image)

    monkeypatch.setattr(vision.startup, 'read', read)
    monkeypatch.setattr(vision, '_opponents', lambda *args, **kwargs: ())
    result = vision.analyze(png)
    assert (result.kind, result.tickets, result.cooldown) == ('tactical', 5, 0)
    assert crops == [(94, 460)]
    assert result.refresh_target == (1174, 147)
    assert result.opponents == ()


@pytest.mark.parametrize('observed,expected', [
    ([], None),
    ([Word('Standby Time', .99, (18, 18, 264, 84))], None),
    ([Word('--:--', .99, (276, 38, 328, 64))], None),
    ([Word('Standby Time', .99, (18, 18, 264, 84)),
      Word('01:72', .99, (276, 38, 390, 64))], None),
    ([Word('Standby Time', .99, (18, 18, 264, 84)),
      Word('01:12', .99, (276, 38, 390, 64))], 72),
])
def test_native_clock_recovery_requires_valid_labeled_observation(vision, monkeypatch, observed, expected):
    png = (FIXTURES / 'tactical-battles-native-missing-clock.png').read_bytes()
    monkeypatch.setattr(vision.startup, 'read', lambda crop: observed)
    assert vision._native_cooldown(decode_native_frame(png)) == expected


@pytest.mark.parametrize('text,score,expected', [
    ('Standby Time --:--', .97, 0), ('Standby Time 01:52', .97, 112),
    ('Standby Time 01:72', .97, None), ('Standby Time --:--', .8, None),
])
def test_cooldown_accepts_complete_label_and_clock_in_one_ocr_box(text, score, expected):
    assert _cooldown((Word(text, score, (57, 515, 216, 541)),)) == expected


@pytest.mark.parametrize('text,confidence,expected', [
    ('Time Left 01:56', .99, 116), ('Time Left 02:00', .99, 120),
    ('Time Left 00:00', .99, 0), ('Time Left 01:59', .8, None),
    ('Time Left 01:69', .99, None), ('Time Left 02:01', .99, 121),
    ('Time Left 02:02', .99, None),
    ('01:59', .99, None)])
def test_refresh_timer_requires_complete_valid_label(vision, text, confidence, expected):
    frame, words = fixture('opponents')
    words = tuple(replace(w, text=text, confidence=confidence)
                  if w.text.startswith('Time Left') else w for w in words)
    result = vision.classify(frame, words)
    assert result.kind == 'tactical'
    assert result.refresh_seconds == expected


@pytest.mark.parametrize('name', ['opponents', 'menu-ready', 'clipped-eight'])
def test_observed_menu_refresh_clock_is_independent_of_team_ocr(vision, name):
    frame, words = fixture(name)
    result = vision.classify(frame, words)
    assert result.refresh_seconds == 119
    words = tuple(w for w in words if not w.text.startswith('Time Left'))
    result = vision.classify(frame, words)
    assert result.kind == 'tactical' and result.refresh_seconds is None


def test_repeated_complete_glyph_conflicts_veto_confident_clipped_label():
    readings = [('', 0.)] * 13
    readings[8] = ('Lv.30', .95409)
    readings[9:11] = [('80', .79954), ('80', .81476)]
    assert _read_level(readings, 81) is None
    # An uncertain contradictory reading alone does not override the label.
    readings[10] = ('80', .7)
    assert _read_level(readings, 81) == 30
    # Conversely, these low-confidence reads cannot authorize a level.
    readings[8] = ('', 0.)
    assert _read_level(readings, 81) is None


def test_actual_confident_clipped_label_cannot_discount_level_80(vision):
    frame, words = fixture('clipped-eight-label')
    result = vision.classify(frame, words)
    assert result.kind == 'tactical'
    assert result.sampled_ranks == (448, 456, 534)
    assert 534 not in [op.choice.rank for op in result.opponents]


def test_observed_refresh_can_display_two_minutes_and_one_second(vision):
    frame, _ = fixture('refresh-121')
    result = vision.classify(frame, tuple(vision.startup.read(frame)))
    assert result.kind == 'tactical'
    assert result.refresh_seconds == 121


def test_missing_formation_timer_or_unreadable_checkbox_does_not_authorize_entry(vision):
    frame, words = fixture('formation-settled')
    words = tuple(w for w in words if w.text != '01:35')
    frame[590:617, 1100:1130] = 0
    result = vision.classify(frame, words)
    assert result.kind == 'formation'
    assert result.formation_seconds is None
    assert result.skip_selected is None


def test_observed_defeat_and_following_tip_have_separate_confirm_states(vision):
    result = vision.classify(*fixture('defeat'))
    assert (result.kind, result.won, result.target) == ('result', False, (640, 660))
    result = vision.classify(*fixture('defeat-tip'))
    assert (result.kind, result.won, result.target) == ('battle_tip', None, (640, 660))
    frame, words = fixture('defeat-tip')
    words = tuple(w for w in words if w.text != 'Special Effects')
    assert vision.classify(frame, words).kind == 'unknown'


def test_observed_skipped_victory_requires_reward_modal_evidence(vision):
    frame, words = fixture('skip-win')
    result = vision.classify(frame, words)
    assert (result.kind, result.won, result.target) == ('result', True, (640, 531))
    assert vision.analyze((FIXTURES / 'tactical-battles-skip-win.png').read_bytes()).kind == 'result'
    for text in ('Battle Result', 'WIN!', 'Rewards', 'Confirm'):
        assert vision.classify(frame, tuple(w for w in words if w.text != text)).kind == 'unknown'
    for changes in ({'text': 'LOSE!'}, {'text': 'WINNER'}, {'confidence': .96},
                    {'box': (530, 225, 590, 250)}):
        changed = tuple(replace(w, **changes) if w.text == 'WIN!' else w for w in words)
        assert vision.classify(frame, changed).kind == 'unknown'
    for bounds in ((205, 327, 505, 775), (498, 561, 533, 752)):
        changed = frame.copy()
        y1, y2, x1, x2 = bounds
        changed[y1:y2, x1:x2] = 0
        assert vision.classify(changed, words).kind == 'unknown'


def test_observed_skipped_loss_requires_its_compact_modal_evidence(vision):
    frame, words = fixture('skip-loss')
    result = vision.classify(frame, words)
    assert (result.kind, result.won, result.target) == ('result', False, (640, 465))
    result = vision.analyze((FIXTURES / 'tactical-battles-skip-loss.png').read_bytes())
    assert (result.kind, result.won, result.target) == ('result', False, (640, 465))
    for text in ('Battle Result', 'LOSE', 'Confirm'):
        changed = tuple(w for w in words if w.text != text)
        assert vision.classify(frame, changed).kind == 'unknown'
    for changes in ({'text': 'WIN'}, {'text': 'CLOSE'}, {'text': 'LOSE!'},
                    {'confidence': .96}, {'box': (530, 280, 590, 305)},
                    {'box': (510, 10, 800, 150)}):
        changed = tuple(replace(w, **changes) if w.text == 'LOSE' else w for w in words)
        assert vision.classify(frame, changed).kind == 'unknown'
    for bounds in ((280, 387, 480, 801), (433, 501, 533, 752),
                   (184, 235, 390, 523)):
        changed = frame.copy()
        y1, y2, x1, x2 = bounds
        changed[y1:y2, x1:x2] = 0
        assert vision.classify(changed, words).kind == 'unknown'


def test_synthetic_win_counterpart_requires_large_exact_title_and_result_controls(vision):
    # Synthetic only: no real Tactical Challenge victory has been captured.
    # Start with the observed result controls; replace the title region and OCR.
    frame, words = fixture('defeat')
    words = tuple(replace(w, text='WIN') if w.text == 'LOSE' else w for w in words)
    # A misread WIN label over the actual red defeat title is never a victory.
    assert vision.classify(frame, words).kind == 'unknown'
    frame[240:420, 440:835] = (55, 55, 55)
    cv2.putText(frame, 'WIN', (475, 382), cv2.FONT_HERSHEY_SIMPLEX,
                4.5, (245, 210, 90), 12, cv2.LINE_AA)
    result = vision.classify(frame, words)
    assert (result.kind, result.won, result.target) == ('result', True, (640, 660))
    for name in ('Time', '01:28', 'Confirm', 'WIN'):
        changed = tuple(w for w in words if w.text != name)
        assert vision.classify(frame, changed).kind == 'unknown'
    for changes in ({'confidence': .96}, {'text': 'WINNER'}, {'text': 'WIN 1'},
                    {'box': (500, 300, 600, 330)}, {'box': (455, 20, 821, 185)}):
        changed = tuple(replace(w, **changes) if w.text == 'WIN' else w for w in words)
        assert vision.classify(frame, changed).kind == 'unknown'
    frame[626:690, 535:740] = 0
    assert vision.classify(frame, words).kind == 'unknown'


def test_live_battle_hud_reports_progress_without_any_input_target(vision):
    result = vision.classify(*fixture('live-hud'))
    assert (result.kind, result.battle_seconds, result.target) == ('battle', 111, None)
    frame, words = fixture('live-hud')
    frame[23:52, 619:661] = 0
    assert vision.classify(frame, words).kind == 'unknown'
