"""Sanitized live assistant cards: variants, weapon stars, and selection proof."""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from ba_automator.assault_assistant_vision import (
    FILTER_TARGETS, read_assistant_filter, read_assistant_metadata,
    read_assistant_page, verify_assistant_preview,
    verify_owned_quick,
)
from ba_automator.vision import StartupVision, Word, decode_frame
from ba_automator.assault_assistant import empty_assistant_slot

FIXTURES = Path(__file__).parent / 'fixtures'


def capture(name):
    frame = decode_frame((FIXTURES / f'assault-assistant-{name}.png').read_bytes())
    words = [Word(w['text'], w['confidence'], tuple(w['box'])) for w in
             json.loads((FIXTURES / f'assault-assistant-{name}.json').read_text())]
    return frame, words


@pytest.fixture(scope='module')
def startup():
    return StartupVision()


def test_gold_base_stars_and_complete_student_variants(startup):
    frame, words = capture('mystic-list')
    page = read_assistant_page(frame, words, startup=startup)
    assert page.kind == 'assistant' and page.available == 1
    assert page.at_top and not page.at_bottom and page.scrollbar
    assert not page.incomplete
    assert [c.member.student_id for c in page.cards] == [
        'Aris (Armed)', 'Shiroko*Terror', 'Hoshino (Armed)', 'Aris (Armed)',
        'Hoshino (Armed)', 'Aris (Armed)']
    assert {c.member.stars for c in page.cards} == {5}
    assert {c.member.level for c in page.cards} == {90}
    assert {c.member.damage_type for c in page.cards} == {'mystic'}
    assert all(c.member.assistant and c.member.role == 'striker' for c in page.cards)
    assert len({c.member.assistant_id for c in page.cards}) == 6
    assert all(len(c.member.assistant_id) == 64 for c in page.cards)
    assert all('Loan' not in repr(c) for c in page.cards)


@pytest.mark.parametrize('local_ocr', [False, True])
def test_real_entry_cleared_only_the_previously_borrowed_slot(startup, local_ocr):
    frame, words = capture('real-quick-empty')
    if local_ocr:
        words = startup.read(frame)
    assert [empty_assistant_slot(words, slot) for slot in range(4)] == [False, True, False, False]
    assert verify_owned_quick(frame, words, startup=startup) is False


def test_weapon_stars_cannot_be_mistaken_for_base_stars(startup):
    frame, words = capture('list')
    page = read_assistant_page(frame, words, startup=startup)
    assert not page.incomplete and len(page.cards) == 6
    assert [c.weapon_stars for c in page.cards] == [4, 4, 4, 3, 4, 3]
    assert {c.member.stars for c in page.cards} == {5}
    assert [c.member.damage_type for c in page.cards] == [
        'explosive', 'mystic', 'explosive', 'explosive', 'explosive', 'piercing']


def test_scrolled_row_is_complete_but_clipped_rows_are_not_candidates(startup):
    frame, words = capture('page2')
    page = read_assistant_page(frame, words, startup=startup)
    assert not page.at_top and not page.at_bottom
    assert [c.member.student_id for c in page.cards] == [
        'Mutsuki (New Year)', 'Kayoko (New Year)', 'Shiroko*Terror', 'Wakamo', 'Kei']
    assert len(page.unavailable) == 1
    frame, words = capture('page3')
    clipped = read_assistant_page(frame, words, startup=startup)
    assert not clipped.cards and clipped.clipped and not clipped.at_bottom


def test_unknown_metadata_remains_incomplete_and_cannot_look_optimal(startup):
    frame, words = capture('mystic-list')
    metadata = list(read_assistant_metadata(frame, startup))
    metadata[0] = replace(metadata[0], stars=None)
    page = read_assistant_page(frame, words, metadata=metadata)
    assert len(page.cards) == 5 and page.incomplete == (metadata[0].bounds,)
    words = [w for w in words if w.text != 'Shiroko*Terror']
    page = read_assistant_page(frame, words, metadata=metadata)
    assert len(page.cards) == 4 and len(page.incomplete) == 2


def test_selection_requires_selected_card_exact_name_and_both_assistant_markers(startup):
    frame, words = capture('aris-preview')
    page = read_assistant_page(frame, words, startup=startup)
    selected = next(c for c in page.cards if c.selected)
    assert verify_assistant_preview(frame, words, selected, 1)
    before, before_words = capture('mystic-list')
    original = read_assistant_page(before, before_words, startup=startup).cards[0]
    assert original.member.assistant_id == selected.member.assistant_id
    assert not verify_assistant_preview(frame, words, replace(selected, selected=False), 1)
    assert not verify_assistant_preview(frame, words, selected, 0)
    wrong = replace(selected, member=replace(selected.member, student_id='Aris (Maid)'))
    assert not verify_assistant_preview(frame, words, wrong, 1)
    frame[558:583, 181:206] = 255
    assert not verify_assistant_preview(frame, words, selected, 1)


@pytest.mark.parametrize('name,selected', [
    ('remove', set(FILTER_TARGETS)), ('mystic-filter', {'mystic'})])
def test_filter_default_gray_checks_differ_from_explicit_attack_filter(name, selected):
    frame, words = capture(name)
    screen = read_assistant_filter(frame, words)
    assert screen.kind == 'assistant_filter' and screen.selected == selected
    assert screen.reset_target == (1110, 162) and screen.confirm_target == (762, 597)
    assert read_assistant_filter((frame * .5).astype(np.uint8), words).kind == 'unknown'


def test_my_students_tab_and_dimmed_background_do_not_authorize_assistant_inputs(startup):
    frame, words = capture('mystic-list')
    assert read_assistant_page((frame*.5).astype(np.uint8), words).kind == 'unknown'
    frame[137:175, 922:957] = (250, 230, 170)
    assert read_assistant_page(frame, words).kind == 'unknown'


@pytest.mark.parametrize('name', ['mystic-list', 'aris-preview'])
def test_full_local_ocr_identifies_sanitized_top_page(startup, name):
    frame, _ = capture(name)
    words = startup.read(frame)
    page = read_assistant_page(frame, words, startup=startup)
    assert page.kind == 'assistant' and not page.incomplete
    assert page.cards[0].member.student_id == 'Aris (Armed)'
    if name == 'aris-preview':
        assert verify_assistant_preview(frame, words, page.cards[0], 1)


def test_auto_team_requires_six_occupied_slots_without_an_assistant_badge(startup):
    frame, words = capture('owned')
    assert verify_owned_quick(frame, words, startup=startup)
    # A missing slot remains unknown even if the neighboring five are readable.
    empty = frame.copy()
    empty[560:639, 114:208] = 255
    assert not verify_owned_quick(empty, words, startup=startup)
    borrowed, borrowed_words = capture('aris-preview')
    assert not verify_owned_quick(borrowed, borrowed_words, startup=startup)


def test_reopened_quick_formation_proves_retained_slot_without_guessing_preview(startup):
    from ba_automator.assault_assistant_vision import verify_retained_assistant
    frame, words = capture('retained')
    page = read_assistant_page(frame, words, startup=startup)
    card = page.cards[0]
    assert card.selected and card.member.student_id == 'Aris (Armed)'
    assert not verify_assistant_preview(frame, words, card, 1)
    assert [verify_retained_assistant(frame, card, slot) for slot in range(4)] == [False, True, False, False]
    assert not verify_retained_assistant(frame, replace(card, selected=False), 1)
    duplicate = frame.copy()
    duplicate[558:583, 271:296] = frame[558:583, 181:206]
    assert not verify_retained_assistant(duplicate, card, 1)
