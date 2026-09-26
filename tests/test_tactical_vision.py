"""Evidence-based ladder recognition; unavailable numbers are never inferred."""

from dataclasses import replace
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_vision import (
    ObservedOpponent, TacticalBattleVision, _cooldown, _read_level,
    same_opponent, same_opponent_identity,
)
from ba_automator.vision import StartupVision, Word

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture(scope='module')
def vision():
    return TacticalBattleVision(StartupVision())


def fixture(name):
    prefix = FIXTURES / f'tactical-battles-{name}'
    frame = cv2.imread(str(prefix.with_suffix('.png')))
    words = tuple(Word(row['text'], row['confidence'], tuple(row['box']))
                  for row in json.loads(prefix.with_suffix('.json').read_text()))
    return frame, words


def test_observed_menu_reads_all_visible_levels_and_hidden_cards(vision):
    result = vision.classify(*fixture('opponents'))
    assert result.kind == 'tactical'
    assert (result.rank, result.tickets, result.cooldown) == (571, 5, 0)
    assert [p.choice.visible_levels for p in result.opponents] == [
        (90, 90, 90), (90, 90, 67), (89, 83, 77)]
    assert [p.choice.rank for p in result.opponents] == [400, 471, 540]
    assert result.sampled_ranks == (400, 471, 540)
    assert result.refresh_seconds == 119


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
    frame[205:230, 740:782] = 0
    result = vision.classify(frame, words)
    assert result.kind == 'tactical' and result.tickets == 5
    assert 400 not in [op.choice.rank for op in result.opponents]
    assert len(result.opponents) == 2


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
            assert len(result.opponents) == 3


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


@pytest.mark.parametrize('text,confidence,expected', [
    ('Time Left 01:56', .99, 116), ('Time Left 02:00', .99, 120),
    ('Time Left 00:00', .99, 0), ('Time Left 01:59', .8, None),
    ('Time Left 01:69', .99, None), ('Time Left 02:01', .99, None),
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
