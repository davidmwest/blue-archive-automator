import json
from pathlib import Path
import pytest
from ba_automator.drill_vision import classify_drill
from ba_automator.vision import Word, decode_frame

FIXTURES = Path(__file__).parent/'fixtures'

def capture(name):
    frame = decode_frame((FIXTURES/f'drill-{name}.png').read_bytes())
    words = [Word(w['text'],w['confidence'],tuple(w['box'])) for w in json.loads((FIXTURES/f'drill-{name}.json').read_text())]
    return frame, words

@pytest.mark.parametrize('name,kind', [('win','result'),('loss','result'),('tip','tip'),('menu','menu'),('detail','detail'),('skills','skills')])
def test_observed_screens(name,kind):
    frame,words=capture(name)
    assert classify_drill(frame,words).kind == kind

def test_results_distinguish_elapsed_time_from_remaining_time():
    win=classify_drill(*capture('win'))
    loss=classify_drill(*capture('loss'))
    assert win.won is True and win.elapsed_seconds == 123.266
    assert loss.won is False and loss.elapsed_seconds == 180

def test_practice_score_does_not_unlock_sweeps():
    menu=classify_drill(*capture('menu'))
    assert menu.mock and menu.active and menu.score == 0 and menu.tickets == 3

def test_missing_result_confirmation_does_not_authorize_a_tap():
    frame,words=capture('win')
    assert classify_drill(frame,[w for w in words if w.normalized!='confirm']).kind != 'result'


def test_active_mock_has_a_yellow_drill_start_button():
    detail = classify_drill(*capture('detail'))
    assert detail.stage == 1
    assert detail.target == (643, 506)
    assert detail.remaining_rounds == 2


def test_mock_settlement_is_not_a_paid_receipt():
    screen = classify_drill(*capture('mock-settlement'))
    assert screen.kind == 'mock_settlement'
    assert screen.mock and screen.score == 57122


def test_entry_confirmation_requires_exact_one_ticket_delta():
    frame, words = capture('entry-confirm')
    screen = classify_drill(frame, words)
    assert screen.kind == 'entry_confirm'
    assert (screen.tickets, screen.tickets_after) == (3, 2)
    from dataclasses import replace
    changed = [replace(w, text='3→1') if '→' in w.text else w for w in words]
    assert classify_drill(frame, changed).kind != 'entry_confirm'


def test_assistant_fee_normalizes_unique_equipment_to_base_rarity():
    frame, words = capture('assistant-fee')
    screen = classify_drill(frame, words)
    assert (screen.credit_fee, screen.assistant_level, screen.assistant_stars) == (40000, 90, 5)
    assert screen.confirm_target == (768, 510)
    assert classify_drill(frame, [w for w in words if w.text != '40,000']).confirm_target is None


def test_paid_settlement_requires_three_round_labels():
    frame, words = capture('settlement')
    screen = classify_drill(frame, words)
    assert screen.kind == 'settlement' and screen.score == 65729
    assert not screen.mock and screen.target == (640, 524)
    missing = [w for w in words if '3rd Formation' not in w.text]
    assert classify_drill(frame, missing).kind != 'settlement'


def test_sweep_requires_matching_count_and_ticket_delta():
    from dataclasses import replace
    frame, words = capture('sweep')
    screen = classify_drill(frame, words)
    assert (screen.kind, screen.count, screen.tickets, screen.tickets_after) == ('sweep', 1, 2, 1)
    changed = [replace(w, text='2→0') if '→' in w.text else w for w in words]
    assert classify_drill(frame, changed).kind != 'sweep'


def test_sweep_confirmation_requires_matching_ticket_and_sweep_counts():
    from dataclasses import replace
    frame, words = capture('sweep-confirm')
    assert classify_drill(frame, words).count == 2
    changed = [replace(w, text=w.text.replace('Use 2', 'Use 1')) for w in words]
    assert classify_drill(frame, changed).kind != 'sweep_confirm'


def test_empty_sweep_panel_can_be_closed_but_not_spent():
    screen = classify_drill(*capture('sweep-empty'))
    assert screen.kind == 'sweep_empty' and screen.count == 0
    assert screen.target == (1087, 196)
