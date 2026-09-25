"""Offline invitation selection, including filters, crop OCR, and scroll safety."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import ba_automator.invitation_picker as picker
from ba_automator.invitations import normalized_identity
from ba_automator.runtime import FRAME_MAX_AGE
from ba_automator.vision import Word

ROOT = Path(__file__).parents[1]


def word(text, x, y, confidence=.99):
    return Word(text, confidence, (x - 15, y - 10, x + 15, y + 10))


def frame(*, direction='descending', search='collapsed', thumb=(200, 228)):
    # The fixture contains only sort/search/scrollbar pixels from the real UI.
    # Everything else, including the account HUD and student portraits, is zero.
    image = cv2.imread(str(ROOT / 'tests/fixtures/invitation-list-controls.png'))
    image[137:169, 804:854] = cv2.imread(str(ROOT / f'ba_automator/assets/invitation-sort-{direction}.png'))
    image[137:169, 616:655] = cv2.imread(str(ROOT / f'ba_automator/assets/invitation-search-{search}.png'))
    image[198:590, 858:863] = 0
    if thumb:
        image[thumb[0]:thumb[1] + 1, 858:863] = [195, 183, 166]
    return image


def row_words(name='Aris (Maid)', rank='26', y=222, control='Invite'):
    words = [word(name, 570, y - 10), word(control, 787, y)]
    if rank is not None:
        words.append(word(rank, 506, y + 20))
    return words


def screen(words=None, *, at=0, **kwargs):
    words = row_words() if words is None else words
    return (at, b'fixture', frame(**kwargs), [word('Relationship Rank', 728, 152), *words])


def empty_search_screen(**kwargs):
    return screen([word('Enter Student Name', 639, 218), *row_words(y=288)], search='expanded', **kwargs)


class Runner:
    def __init__(self):
        self.now = 0
        self.journal = SimpleNamespace(record=Mock())
        self.startup = SimpleNamespace(read=Mock(return_value=[]))
        self.device = SimpleNamespace(swipe=Mock(return_value=True))
        self.tap = Mock()
        self.word_screens = []
        self.floor = 1

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def fail(self, message):
        raise RuntimeError(message)

    def wait_words(self, predicate, **kwargs):
        value = self.word_screens.pop(0)
        assert predicate(value[3])
        return value


@pytest.mark.parametrize('direction', ['ascending', 'descending'])
def test_sort_direction_recognizes_real_ui_crops(direction):
    assert picker.sort_direction(frame(direction=direction)) == direction


def test_sort_direction_unknown_and_ambiguous_images_never_default_descending():
    assert picker.sort_direction(np.zeros((720, 1280, 3), np.uint8)) is None
    first, second = frame(direction='ascending'), frame(direction='descending')
    first[137:169, 804:854] = ((first[137:169, 804:854].astype(float)
                              + second[137:169, 804:854]) / 2).astype(np.uint8)
    assert picker.sort_direction(first) is None


@pytest.mark.parametrize('state', ['expanded', 'collapsed'])
def test_search_state_recognizes_actual_empty_and_expanded_button_pixels(state):
    assert picker.search_state(frame(search=state)) == state


def test_search_state_unknown_is_not_treated_as_collapsed():
    assert picker.search_state(np.zeros((720, 1280, 3), np.uint8)) is None


def test_real_scrollbar_fixture_is_at_top():
    image = cv2.imread(str(ROOT / 'tests/fixtures/invitation-list-controls.png'))
    top, bottom = picker.scrollbar(image)
    assert 198 <= top <= 203
    assert bottom > top + 12


@pytest.mark.parametrize('thumb', [(200, 228), (390, 425), (563, 589)])
def test_scrollbar_reads_top_middle_bottom(thumb):
    assert picker.scrollbar(frame(thumb=thumb)) == thumb


def test_scrollbar_missing_short_or_fragmented_stays_unknown():
    assert picker.scrollbar(frame(thumb=None)) is None
    assert picker.scrollbar(frame(thumb=(200, 210))) is None
    image = frame(thumb=(200, 228))
    image[212, 858:863] = 0
    assert picker.scrollbar(image) is None


def test_prepare_explicitly_checks_hidden_search_filter_then_collapses():
    runner = Runner()
    collapsed, expanded = screen(), empty_search_screen()
    wait_list = Mock(side_effect=[expanded, collapsed])
    assert picker.prepare_list(runner, collapsed, wait_list) is collapsed
    assert [call.args[1] for call in runner.tap.call_args_list] == [(631, 151), (631, 151)]
    runner.device.swipe.assert_not_called()


def test_nonempty_filter_cannot_select_a_lower_student_even_when_scrollbar_exists():
    runner = Runner()
    filtered = screen([word('Hina', 639, 218), *row_words('Hina', '23', y=288)], search='expanded')
    with pytest.raises(RuntimeError, match='search filter'):
        picker.prepare_list(runner, screen(), Mock(return_value=filtered))
    assert runner.tap.call_count == 1


@pytest.mark.parametrize('placeholder', [word('Enter Student Name', 639, 218, .6),
                                         word('Enter Student Name', 100, 600)])
def test_wrong_or_low_confidence_placeholder_does_not_prove_unfiltered_list(placeholder):
    runner = Runner()
    expanded = screen([placeholder, *row_words(y=288)], search='expanded')
    with pytest.raises(RuntimeError, match='search filter'):
        picker.prepare_list(runner, expanded, Mock())
    runner.tap.assert_not_called()


def test_expanded_search_must_actually_close():
    runner = Runner()
    expanded = empty_search_screen()
    with pytest.raises(RuntimeError, match='did not close'):
        picker.prepare_list(runner, expanded, Mock(return_value=expanded))


def test_prepare_ascending_list_changes_direction_and_verifies_it():
    runner = Runner()
    ascending, descending = screen(direction='ascending'), screen()
    runner.word_screens = [descending]
    result = picker.prepare_list(runner, empty_search_screen(direction='ascending'), Mock(return_value=ascending))
    assert result is descending
    assert runner.tap.call_args_list[-1].args[1] == (830, 152)


def test_prepare_scrolls_to_verified_top_before_returning():
    runner = Runner()
    middle, top = screen(thumb=(390, 425)), screen()
    result = picker.prepare_list(runner, empty_search_screen(), Mock(side_effect=[middle, top]))
    assert result is top
    runner.device.swipe.assert_called_once()
    assert runner.device.swipe.call_args.args == ((650, 265), (650, 545))


def test_prepare_cannot_treat_no_scrollbar_as_top():
    with pytest.raises(RuntimeError, match='scrollbar'):
        picker.prepare_list(Runner(), empty_search_screen(), Mock(return_value=screen(thumb=None)))


def test_scroll_uses_frame_deadline_and_records_stale_input():
    runner = Runner()
    runner.device.swipe.return_value = False
    with pytest.raises(RuntimeError, match='expired'):
        picker.scroll(runner, screen(at=20), down=True)
    assert runner.device.swipe.call_args.kwargs['deadline'] == 20 + FRAME_MAX_AGE
    assert runner.journal.record.call_args.kwargs['result'] == 'skipped_stale'


def test_known_ranks_do_not_invoke_extra_ocr():
    runner = Runner()
    assert picker.read_rows(runner, screen())[0]['rank'] == 26
    runner.startup.read.assert_not_called()


def test_missing_rank_uses_enlarged_heart_crop_only():
    runner = Runner()
    runner.startup.read.return_value = [word('26', 82, 70)]
    rows = picker.read_rows(runner, screen(row_words(rank=None)))
    assert rows[0]['rank'] == 26
    assert runner.startup.read.call_args.args[0].shape == (140, 164, 3)


@pytest.mark.parametrize('found', [[word('26', 82, 70, .6)], [word('26x', 82, 70)],
                                  [word('26.5', 82, 70)], [word('0', 82, 70)],
                                  [word('101', 82, 70)], [word('26', 60, 60), word('28', 90, 90)],
                                  [word('26', 60, 60), word('2O', 90, 90)], []])
def test_ambiguous_or_malformed_crop_ocr_remains_unknown(found):
    runner = Runner()
    runner.startup.read.return_value = found
    assert picker.read_rows(runner, screen(row_words(rank=None)))[0]['rank'] is None


def test_conflicting_full_frame_digits_are_not_overridden_by_favorable_crop():
    runner = Runner()
    runner.startup.read.return_value = [word('26', 82, 70)]
    words = row_words() + [word('28', 506, 240)]
    assert picker.read_rows(runner, screen(words))[0]['rank'] is None
    runner.startup.read.assert_not_called()


def test_low_confidence_name_cannot_be_rescued_by_good_digit_crop():
    runner = Runner()
    words = row_words(rank=None)
    words[0] = word('Aris (Maid)', 570, 212, .6)
    assert picker.read_rows(runner, screen(words))[0]['rank'] is None
    runner.startup.read.assert_not_called()


def test_bottom_partial_rank_is_not_read_across_the_fixed_tip_overlay():
    runner = Runner()
    assert picker.read_rows(runner, screen(row_words(rank=None, y=587)))[0]['rank'] is None
    runner.startup.read.assert_not_called()


def test_select_refreshes_candidate_coordinates_and_logs_decision(monkeypatch):
    monkeypatch.setattr(picker, 'prepare_list', lambda runner, value, wait: value)
    runner = Runner()
    initial, fresh = screen(), screen(row_words(y=224), at=50)
    result = picker.select_automatic(runner, initial, Mock(return_value=fresh))
    assert result[0] is fresh
    assert result[1]['target'] == (787, 224)
    assert result[1]['rank'] == 26
    assert runner.journal.record.call_args.kwargs['student'] == 'Aris (Maid)'
    runner.tap.assert_not_called()


@pytest.mark.parametrize('fresh', [screen(row_words('Tsubaki', '24')),
                                   screen(row_words(control='Invited')),
                                   screen(search='expanded'), screen(direction='ascending')])
def test_changed_recipient_or_controls_cannot_be_selected(monkeypatch, fresh):
    monkeypatch.setattr(picker, 'prepare_list', lambda runner, value, wait: value)
    with pytest.raises(RuntimeError, match='changed'):
        picker.select_automatic(Runner(), screen(), Mock(return_value=fresh))


def test_unreadable_highest_student_does_not_choose_lower_student(monkeypatch):
    monkeypatch.setattr(picker, 'prepare_list', lambda runner, value, wait: value)
    words = row_words(rank=None) + row_words('Tsubaki', '24', y=300)
    with pytest.raises(RuntimeError, match='unreadable'):
        picker.select_automatic(Runner(), screen(words), Mock())


def test_ascending_rank_evidence_blocks_even_with_descending_arrow(monkeypatch):
    monkeypatch.setattr(picker, 'prepare_list', lambda runner, value, wait: value)
    words = row_words(rank='24') + row_words('Tsubaki', '26', y=300)
    with pytest.raises(RuntimeError, match='out of order'):
        picker.select_automatic(Runner(), screen(words), Mock())


def test_page_overlap_is_checked_before_selecting_new_candidate(monkeypatch):
    monkeypatch.setattr(picker, 'prepare_list', lambda runner, value, wait: value)
    first = screen(row_words(rank='100'))
    # A large jump could skip higher eligible students. Refuse the later one.
    later = screen(row_words('Tsubaki', '24'), thumb=(390, 425))
    with pytest.raises(RuntimeError, match='lost row overlap'):
        picker.select_automatic(Runner(), first, Mock(return_value=later))


def test_all_maxed_returns_none_only_after_reaching_verified_bottom(monkeypatch):
    monkeypatch.setattr(picker, 'prepare_list', lambda runner, value, wait: value)
    last = screen(row_words(rank='100'), thumb=(563, 589))
    runner = Runner()
    assert picker.select_automatic(runner, last, Mock()) is None
    runner.tap.assert_not_called()


def test_current_rarity_lookup_reopens_original_floor_and_rechecks_list(monkeypatch):
    import ba_automator.invitation_roster as roster

    monkeypatch.setattr(picker, 'prepare_list', lambda runner, value, wait: value)
    read_stars = Mock(return_value=5)
    monkeypatch.setattr(roster, 'read_current_stars', read_stars)
    runner = Runner()
    runner.floor = 2
    runner.wait_cafe = Mock(return_value=screen())
    runner.navigate = Mock()
    runner.enter = Mock()
    runner.wait_floor = Mock(return_value=screen([word('Move to Cafe No. 2', 150, 101)]))
    runner.move_to_floor = Mock()
    boundary = screen(row_words(rank='30'))
    result = picker.select_automatic(runner, boundary, Mock(return_value=boundary))
    read_stars.assert_called_once_with(runner, 'Aris (Maid)')
    runner.move_to_floor.assert_called_once_with(2)
    assert result[1]['identity'] == normalized_identity('Aris (Maid)')
    assert runner.journal.record.call_args.kwargs['current_stars'] == 5
