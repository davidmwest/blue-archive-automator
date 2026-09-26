"""Saved-team preservation and verified highest-level owned student selection."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from ba_automator import tactical_formation as formation
from ba_automator.vision import Word, StartupVision, decode_frame

FIXTURES = Path(__file__).parent / 'fixtures'


def capture(name):
    image = decode_frame((FIXTURES / f'tactical-{name}.png').read_bytes())
    words = tuple(Word(w['text'], w['confidence'], tuple(w['box']))
                  for w in json.loads((FIXTURES / f'tactical-{name}.json').read_text()))
    return image, words


def frame(name):
    image, words = capture(name)
    return SimpleNamespace(capture=SimpleNamespace(png=cv2.imencode('.png', image)[1].tobytes()),
                           screen=SimpleNamespace(words=words))


def test_complete_saved_team_requires_no_input():
    class Runner(formation.TacticalFormationMixin):
        def tap(self, *args):
            pytest.fail('A complete saved team must not be edited')
        def fail(self, message):
            pytest.fail(message)
    original = frame('attack-formation')
    assert Runner().fill_attack_formation(original) is original
    slots = formation.read_attack_slots(*capture('attack-formation'))
    assert [s.student_id for s in slots] == ['Shun', 'Aris (Maid)', 'Tsubaki', 'Eimi (Armed)',
                                           'Michiru (Dress)', 'Serina']


@pytest.mark.parametrize('remove', ['Shun', 'Lv.79'])
def test_missing_ordinary_text_is_never_an_empty_slot(remove):
    image, words = capture('attack-formation')
    assert formation.read_attack_slots(image, [w for w in words if w.text != remove]) is None


def test_full_ocr_formation_help_icon_does_not_hide_saved_team():
    image = decode_frame((FIXTURES / 'tactical-battles-formation-settled.png').read_bytes())
    words = StartupVision().read(image)
    slots = formation.read_attack_slots(image, words)
    assert slots is not None and len(slots) == 6 and all(slots)
    assert [student.student_id for student in slots] == [
        'Shun', 'Aris (Maid)', 'Tsubaki', 'Eimi (Armed)', 'Michiru (Dress)', 'Serina']


def test_explicit_ordinary_role_blank_and_no_level_are_required():
    image, words = capture('attack-formation')
    words = [w for w in words if not (204 <= w.center[0] <= 395 and 517 <= w.center[1] <= 574)]
    words.append(Word('STRIKER Slot', .99, (265, 530, 389, 554)))
    result = formation.read_attack_slots(image, words)
    assert result[0] is None and all(result[1:])
    words.append(Word('Lv.79', .99, (210, 548, 254, 568)))
    assert formation.read_attack_slots(image, words) is None


@pytest.mark.parametrize('name,role,empty,selected', [
    ('quick', 'striker', (), (True, True, True, True, False, False)),
    ('quick-empty', 'striker', (0,), (True, True, False, True, False, False)),
    ('quick-restored', 'striker', (), (True, True, True, True, False, False)),
    ('quick-special', 'special', (), (True, True, False, False, False, False)),
])
def test_live_editor_geometry(name, role, empty, selected):
    image, words = capture(name)
    editor = formation.read_editor(image, words)
    assert editor.kind == 'quick' and editor.role == role
    assert editor.empty == empty and editor.selected == selected
    assert editor.at_top and editor.descending
    assert formation.read_attack_slots(image, words) is None


def test_filter_reset_requires_every_attack_and_defense_checkbox():
    image, words = capture('formation-display')
    assert formation.read_editor(image, words).all_filters
    for x, y in formation.FILTER_POINTS:
        missing = image.copy()
        missing[y-18:y+18, x-18:x+18] = 255
        assert not formation.read_editor(missing, words).all_filters


def test_level_sort_and_roster_direction_are_independent_proofs():
    image, words = capture('formation-sort')
    editor = formation.read_editor(image, words)
    assert editor.kind == 'sort' and editor.level_sort
    image[177:191, 285:299] = 255
    assert not formation.read_editor(image, words).level_sort
    image, words = capture('quick')
    image[141:179, 1112:1180] = 255
    assert not formation.read_editor(image, words).descending


def test_highest_unselected_card_keeps_observed_tie_order():
    image, words = capture('quick-empty')
    editor = formation.read_editor(image, words)
    result = formation.highest_available(image, words, editor, (79,)*6, {'Aris (Maid)', 'Eimi (Armed)', 'Tsubaki'})
    assert result[0] == formation.Student('Shun', 'striker', 79)
    assert result[1] == (838, 271)
    assert formation.highest_available(image, words, replace(editor, at_top=False), (79,)*6, set()) is None
    assert formation.highest_available(image, words, replace(editor, descending=False), (79,)*6, set()) is None
    assert formation.highest_available(image, words, editor, (79, 79, None, 79, 79, 79), set()) is None
    assert formation.highest_available(image, words, editor, (79,)*6, {'Shun'}) is None


def test_unreadable_higher_available_card_cannot_be_skipped():
    image, words = capture('quick-empty')
    editor = formation.read_editor(image, words)
    words = [w for w in words if not (785 <= w.center[0] <= 892 and 294 <= w.center[1] <= 345)]
    assert formation.highest_available(image, words, editor, (79,)*6, set()) is None


def test_live_selection_preserves_all_preexisting_portraits():
    before, _ = capture('quick-empty')
    after, _ = capture('quick-restored')
    assert formation.unchanged_portraits(before, after, range(1, 6))
    assert not formation.unchanged_portraits(before, after, range(6))
    after[584:618, 158:203] = 0
    assert not formation.unchanged_portraits(before, after, range(1, 6))


@pytest.mark.parametrize('name', ['quick-empty', 'quick-special'])
def test_local_level_ocr_on_real_roster(name):
    image, words = capture(name)
    levels = formation.read_roster_levels(image, StartupVision())
    assert levels == (79,)*6


def runner_frame(name):
    result = frame(name)
    result.capture.deadline = 5
    return result


def test_fill_one_blank_preserves_five_students_and_verifies_final_team(monkeypatch):
    original = list(formation.read_attack_slots(*capture('attack-formation')))
    original[0] = None
    inputs, events = [], []

    class Runner(formation.TacticalFormationMixin):
        vision = SimpleNamespace(startup=None)
        journal = SimpleNamespace(record=lambda *args, **kwargs: events.append((args, kwargs)))
        def clock(self):
            return 0
        def tap(self, frame, point, detail):
            inputs.append(point)
        def fail(self, message):
            raise RuntimeError(message)
        def _editor_wait(self, *args, predicate=lambda e: True, **kwargs):
            name = 'quick-empty' if len(inputs) == 1 else 'quick-restored'
            result = runner_frame(name)
            editor = formation.read_editor(*capture(name))
            assert predicate(editor)
            return result, editor
        def _prepare_roster(self, current, editor, role):
            assert role == 'striker'
            return current, editor
        def wait(self, kind):
            assert kind == 'formation'
            return runner_frame('attack-formation')

    monkeypatch.setattr(formation, 'read_roster_levels', lambda *args: (79,)*6)
    result = Runner()._fill_attack_slots(runner_frame('attack-formation'), tuple(original))
    assert inputs == [(1203, 162), (838, 271), (1130, 595)]
    assert formation.read_attack_slots(decode_frame(result.capture.png), result.screen.words)[0].student_id == 'Shun'
    assert events == [(('tactical_formation_filled',), dict(
        students=[dict(slot=0, student='Shun', level=79)], preserved=5))]


def test_filter_setup_handles_retained_sort_tab():
    inputs = []
    quick = formation.read_editor(*capture('quick-empty'))
    stages = iter([
        formation.FormationEditor('sort', level_sort=True),
        formation.FormationEditor('filter', all_filters=True),
        formation.FormationEditor('filter', all_filters=True),
        formation.FormationEditor('sort', level_sort=True),
        formation.FormationEditor('sort', level_sort=True), quick,
    ])
    class Runner(formation.TacticalFormationMixin):
        def tap(self, frame, point, detail):
            inputs.append(point)
        def _editor_wait(self, kind='quick', *, predicate=lambda e: True):
            stage = next(stages)
            assert stage.kind in ({kind} if isinstance(kind, str) else kind)
            assert predicate(stage)
            return None, stage
        def _roster_top(self, frame, editor):
            return frame, editor
    assert Runner()._prepare_roster(None, quick, 'striker')[1] is quick
    assert inputs == [(978, 160), (145, 154), (1110, 162), (145, 214), (332, 184), (762, 597)]
