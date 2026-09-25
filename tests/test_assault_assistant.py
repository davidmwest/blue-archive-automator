"""Assistant choice proofs and fail-closed serial selection, without device input."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ba_automator.assault_assistant import AssaultAssistantMixin, best_at_observed_ceiling, empty_assistant_slot
from ba_automator.assault_assistant_vision import AssistantCard, AssistantPage
from ba_automator.assault_policy import MockResult, TeamMember
from ba_automator.vision import Word


def card(name='borrowed', *, column=0, stars=5, level=90, damage='mystic', selected=False, lender='loan'):
    x = 574 + column*111
    return AssistantCard(TeamMember(name, 0, 'striker', damage, stars, level, True, lender),
                         (x, 245, x+107, 416), (x+54, 300), selected)


def page(cards=(), **fields):
    return replace(AssistantPage(kind='assistant', cards=tuple(cards), at_top=True, available=1,
                                 scrollbar=(243, 333), level_descending=True, rows=(('row-a', True),),
                                 confirm_target=(1168, 593)), **fields)


def frame(screen):
    return SimpleNamespace(screen=screen, capture=SimpleNamespace(png=b'image'))


def team():
    return tuple(TeamMember(f'student-{slot}', slot, 'striker' if slot < 4 else 'special',
                            'mystic', 3, 80) for slot in range(6))


def test_maximum_base_stars_at_observed_highest_sorted_level_is_a_complete_proof():
    first, second = card(stars=4, level=120), card('other', column=1, level=120, lender='second')
    assert best_at_observed_ceiling(page((first, second)), 'mystic', team()[1:]) == second
    # The cap comes from the screen; it is not the current game's level cap.
    assert best_at_observed_ceiling(page((card(level=79),)), 'mystic', team()[1:]) is not None


@pytest.mark.parametrize('changes', [
    {'at_top': False}, {'level_descending': False}, {'cards': ()},
    {'incomplete': ((500, 245, 600, 416),)},
    {'cards': (card(stars=4),)},
    {'cards': (card(level=80), card('higher', column=1, level=90))},
    {'cards': (card(damage='explosive'),)},
    {'cards': (card('student-1'),)},
])
def test_partial_unsorted_or_nonmatching_page_is_not_a_global_optimum(changes):
    assert best_at_observed_ceiling(replace(page((card(),)), **changes), 'mystic', team()[1:]) is None


class SurveyRunner(AssaultAssistantMixin):
    def __init__(self, first, following):
        self.first = first
        self.following = iter(following)
        self.swipes = []
        self.records = []
        self.journal = SimpleNamespace(record=lambda kind, **fields: self.records.append((kind, fields)))

    def _assistant_start(self, current):
        return self.first

    def wait_assistant(self):
        return next(self.following)

    def swipe(self, current, start, end, detail):
        self.swipes.append((start, end))

    def fail(self, message):
        raise RuntimeError(message)


def test_complete_overlapping_survey_selects_highest_stars_before_level_and_refinds_offering():
    lower = card(stars=3, level=95)
    best = card('winner', stars=4, level=80, lender='exact-winner')
    first = frame(page((lower,)))
    bottom = frame(page((best,), at_top=False, at_bottom=True, scrollbar=(422, 514),
                        rows=(('row-a', False), ('row-b', True))))
    runner = SurveyRunner(first, [bottom, bottom])
    found, current = runner._survey_assistants(first, 'mystic', team()[1:])
    assert found == best and current is bottom
    assert len(runner.swipes) == 2
    assert runner.records[-1][1]['proof'] == 'bounded_top_to_bottom_scan'


@pytest.mark.parametrize('scrollbar,relocation_swipes,message', [
    ((243, 333), 3, 'scrolling stalled while locating'),
    (None, 0, 'scroll position became unreadable'),
])
def test_complete_survey_relocation_stops_if_scroll_progress_cannot_be_verified(
        scrollbar, relocation_swipes, message):
    first = frame(page((card(stars=3),)))
    bottom = frame(page((card('winner', stars=4, lender='winner-loan'),), at_top=False,
                        at_bottom=True, scrollbar=(422, 514),
                        rows=(('row-a', False), ('row-b', True))))
    stuck = frame(replace(first.screen, scrollbar=scrollbar))
    runner = SurveyRunner(first, [bottom, stuck, stuck, stuck])
    starts = iter((first, stuck))
    runner._assistant_start = lambda _: next(starts)
    with pytest.raises(RuntimeError, match=message):
        runner._survey_assistants(first, 'mystic', team()[1:])
    assert len(runner.swipes) == 1 + relocation_swipes
    assert runner.records[-1][1]['proof'] == 'bounded_top_to_bottom_scan'


@pytest.mark.parametrize('fields,message', [
    ({'incomplete': ((574, 245, 681, 416),)}, 'could not be read'),
    ({'scrollbar': None}, 'could not be read'),
    ({'rows': ()}, 'overlap is unverified'),
    ({'rows': (('skipped-row', True),)}, 'overlap is unverified'),
    ({'rows': (('row-a', False), ('row-b', False))}, 'partially visible'),
    ({'rows': (('row-a', True), ('row-a', True))}, 'overlap is unverified'),
])
def test_survey_never_claims_optimality_with_missing_rows_or_unreadable_cards(fields, message):
    first = frame(page((card(stars=3),)))
    bottom = frame(replace(page((card('later', stars=4),), at_top=False, at_bottom=True,
                                scrollbar=(423, 514)), **fields))
    runner = SurveyRunner(first, [bottom])
    with pytest.raises(RuntimeError, match=message):
        runner._survey_assistants(first, 'mystic', team()[1:])
    assert not runner.records


def test_maximum_candidate_avoids_unnecessary_scroll_and_does_not_need_lower_unreadable_cards():
    top = frame(page((card(),), incomplete=((1129, 245, 1236, 416),)))
    runner = SurveyRunner(top, [])
    found, _ = runner._survey_assistants(top, 'mystic', team()[1:])
    assert found.member.stars == 5 and not runner.swipes
    assert runner.records[-1][1]['proof'] == 'maximum_stars_at_highest_sorted_level'


class VerificationRunner(AssaultAssistantMixin):
    def __init__(self, assistant_page, final_team):
        self.selected = frame(assistant_page)
        self.final = frame(SimpleNamespace(kind='formation', team=final_team))
        self.taps = []
        self.assistant = None

    def tap(self, current, target, detail):
        self.taps.append(target)

    def wait(self, kind, *, predicate=lambda screen: True):
        result = (frame(SimpleNamespace(kind='quick', assistant_target=(1083, 156),
                                       confirm_target=(1168, 593), words=()))
                  if kind == 'quick' else self.final)
        assert predicate(result.screen)
        return result

    def wait_assistant(self):
        return self.selected

    def _assistant_start(self, current):
        return current

    def fail(self, message):
        raise RuntimeError(message)


def borrowed_team():
    original = team()
    return original[:1] + (replace(card().member, slot=1),) + original[2:]


def real_formation(*, members=None):
    members = members if members is not None else tuple(
        replace(member, assistant=False, assistant_id=None) for member in borrowed_team())
    return frame(SimpleNamespace(quick_target=(1205, 182), team=members))


def test_real_verification_binds_same_lender_and_slot_without_changing_the_selected_card(monkeypatch):
    from ba_automator import assault_assistant
    expected = borrowed_team()
    observed = tuple(replace(member, assistant=False, assistant_id=None) for member in expected)
    runner = VerificationRunner(page((card(selected=True),)), observed)
    verified = []
    monkeypatch.setattr(assault_assistant, 'decode_frame', lambda png: 'decoded')
    monkeypatch.setattr(assault_assistant, 'verify_retained_assistant',
                        lambda image, selected, slot: verified.append((selected.member.assistant_id, slot)) or True)
    monkeypatch.setattr(assault_assistant, 'verify_assistant_preview',
                        lambda *args: pytest.fail('Reopening Quick clears the inspection panel'))
    result = runner.verify_real_assistant(real_formation(), expected)
    assert result is runner.final
    assert runner.assistant == expected[1] and verified == [('loan', 1)]
    assert runner.taps == [(1205, 182), (1083, 156), (1168, 593)]


@pytest.mark.parametrize('selected,preview,changed', [
    (card(selected=False), True, False),
    (card(selected=True, lender='different-lender'), True, False),
    (card(selected=True, level=89), True, False),
    (card(selected=True), False, False),
    (card(selected=True), True, True),
])
def test_ambiguous_real_assistant_holds_intent_instead_of_mobilizing(monkeypatch, selected, preview, changed):
    from ba_automator import assault_assistant
    expected = borrowed_team()
    observed = tuple(replace(member, assistant=False, assistant_id=None) for member in expected)
    if changed:
        observed = (replace(observed[0], level=79),) + observed[1:]
    runner = VerificationRunner(page((selected,), at_bottom=True), observed)
    monkeypatch.setattr(assault_assistant, 'decode_frame', lambda png: 'decoded')
    monkeypatch.setattr(assault_assistant, 'verify_assistant_preview', lambda *args: preview)
    monkeypatch.setattr(assault_assistant, 'verify_retained_assistant', lambda *args: preview)
    with pytest.raises(RuntimeError, match='ticket intent is held'):
        runner.verify_real_assistant(real_formation(), expected)
    assert runner.assistant is None
    assert all(target in ((1205, 182), (1083, 156), (1168, 593)) for target in runner.taps)


EMPTY_SLOT_ONE = (Word('EMPTY', .999, (129, 590, 189, 610)),)


@pytest.mark.parametrize('words,slot,expected', [
    (EMPTY_SLOT_ONE, 1, True), (EMPTY_SLOT_ONE, 0, False), ((), 1, False),
    ((Word('EMPTY', .5, (129, 590, 189, 610)),), 1, False),
    (EMPTY_SLOT_ONE + (Word('EMPTY', .99, (309, 590, 369, 610)),), 1, False),
])
def test_restoration_requires_one_readable_empty_slot_in_exact_position(words, slot, expected):
    assert empty_assistant_slot(words, slot) is expected


class RestorationRunner(VerificationRunner):
    def __init__(self, offered=None, *, empty_words=EMPTY_SLOT_ONE):
        expected = borrowed_team()
        observed = tuple(replace(member, assistant=False, assistant_id=None) for member in expected)
        offered = offered or card()
        super().__init__(page((offered,), words=empty_words, at_bottom=True), observed)
        self.empty_words = empty_words
        self.offering = offered
        self.restored = False
        self.filtered = []

    def wait(self, kind, *, predicate=lambda screen: True):
        if kind == 'quick':
            result = frame(SimpleNamespace(kind='quick', assistant_target=(1083, 156), words=self.empty_words))
            assert predicate(result.screen)
            return result
        return super().wait(kind, predicate=predicate)

    def tap(self, current, target, detail):
        super().tap(current, target, detail)
        if target == self.offering.target:
            assert empty_assistant_slot(current.screen.words, 1)
            self.restored = True
            self.selected = frame(page((replace(self.offering, selected=True),)))

    def filter_assistants(self, current, damage_type):
        self.filtered.append(damage_type)
        return current

    def wait_assistant(self, *, predicate=lambda screen: True):
        assert predicate(self.selected.screen)
        return self.selected


def test_real_entry_restores_only_exact_qualified_offering_into_empty_slot(monkeypatch):
    from ba_automator import assault_assistant
    runner = RestorationRunner()
    monkeypatch.setattr(assault_assistant, 'decode_frame', lambda png: 'decoded')
    monkeypatch.setattr(assault_assistant, 'verify_assistant_preview',
                        lambda image, words, selected, slot: selected.selected and slot == 1)
    result = runner.verify_real_assistant(real_formation(members=()), borrowed_team())
    assert result is runner.final and runner.restored and runner.assistant == borrowed_team()[1]
    assert runner.filtered == ['mystic']
    assert runner.taps == [(1205, 182), (1083, 156), (628, 300), (1168, 593)]


@pytest.mark.parametrize('offered,empty_words', [
    (card(lender='other-lender'), EMPTY_SLOT_ONE),
    (card(level=89), EMPTY_SLOT_ONE),
    (card(), (Word('EMPTY', .999, (309, 590, 369, 610)),)),
    (card(selected=True), EMPTY_SLOT_ONE),
])
def test_real_restoration_cannot_substitute_lender_stats_or_another_slot(offered, empty_words):
    runner = RestorationRunner(offered, empty_words=empty_words)
    with pytest.raises(RuntimeError, match='ticket intent is held'):
        runner.verify_real_assistant(real_formation(members=()), borrowed_team())
    assert not runner.restored and runner.assistant is None
    assert (1168, 593) not in runner.taps


@pytest.mark.parametrize('members', [(), team()])
def test_occupied_formation_must_match_mock_before_searching_assistants(members):
    runner = VerificationRunner(page((card('wrong student', selected=True),)), members)
    with pytest.raises(RuntimeError, match='occupied real formation differs'):
        runner.verify_real_assistant(real_formation(members=members), borrowed_team())
    assert runner.taps == [(1205, 182)]


def test_selected_wrong_lender_fails_before_any_search_swipe():
    runner = VerificationRunner(page((card(selected=True, lender='wrong lender'),)), ())
    runner.swipe = lambda *args: pytest.fail('A visibly selected wrong offering must not start a search')
    with pytest.raises(RuntimeError, match='different assistant is selected'):
        runner.verify_real_assistant(real_formation(), borrowed_team())
    assert (1168, 593) not in runner.taps


def test_real_assistant_search_stops_when_viewport_does_not_move():
    runner = VerificationRunner(page((card('different student'),)), ())
    swipes = []
    runner.swipe = lambda *args: swipes.append(args)
    with pytest.raises(RuntimeError, match='scrolling stalled'):
        runner.verify_real_assistant(real_formation(), borrowed_team())
    assert len(swipes) == 3
    assert (1168, 593) not in runner.taps


def test_scroll_to_top_stops_when_thumb_does_not_move():
    runner = VerificationRunner(page((), at_top=False, scrollbar=(250, 280)), ())
    swipes = []
    runner.swipe = lambda *args: swipes.append(args)
    with pytest.raises(RuntimeError, match='stalled before the top'):
        AssaultAssistantMixin._assistant_start(runner, runner.selected)
    assert len(swipes) == 3


def test_owned_real_team_rechecks_no_borrowed_badges_and_complete_final_formation(monkeypatch):
    from ba_automator import assault_assistant
    expected = team()
    runner = VerificationRunner(page(), expected)
    runner.vision = SimpleNamespace(startup=object())
    runner.assistant = card().member  # Stale process state is cleared by proof.
    checked = []
    monkeypatch.setattr(assault_assistant, 'decode_frame', lambda png: 'decoded')
    monkeypatch.setattr(assault_assistant, 'verify_owned_quick',
                        lambda image, words, *, startup: checked.append(image) or True)
    result = runner.verify_real_owned(real_formation(members=expected), expected)
    assert result is runner.final and runner.assistant is None and checked == ['decoded']
    assert runner.taps == [(1205, 182), (1168, 593)]


@pytest.mark.parametrize('borrowed_badge,changed', [(True, False), (False, True)])
def test_owned_real_verification_rejects_same_metadata_borrowed_copy_or_post_confirm_change(
        monkeypatch, borrowed_badge, changed):
    from ba_automator import assault_assistant
    expected = team()
    final = (replace(expected[0], level=79), *expected[1:]) if changed else expected
    runner = VerificationRunner(page(), final)
    runner.vision = SimpleNamespace(startup=object())
    monkeypatch.setattr(assault_assistant, 'decode_frame', lambda png: 'decoded')
    monkeypatch.setattr(assault_assistant, 'verify_owned_quick', lambda *args, **kwargs: not borrowed_badge)
    with pytest.raises(RuntimeError, match='contains an assistant|owned formation changed'):
        runner.verify_real_owned(real_formation(members=expected), expected)
    if borrowed_badge:
        assert runner.taps == [(1205, 182)]


class FallbackRunner(VerificationRunner):
    def __init__(self):
        expected = borrowed_team()
        owned = tuple(replace(member, assistant=False, assistant_id=None) for member in expected)
        super().__init__(page((card(selected=True),)), owned)
        self.formations = iter((frame(SimpleNamespace(kind='formation', team=team(), quick_target=(1205, 182))), self.final))
        self.calls = []
        self.context_checked = False

    def check_context(self, detail):
        self.context_checked = True

    def wait(self, kind, *, predicate=lambda screen: True):
        if kind == 'formation':
            result = next(self.formations)
        else:
            result = frame(SimpleNamespace(kind='quick', assistant_target=(1083, 156),
                            words=(Word('EMPTY', 1.0, (118, 580, 200, 610)),)))
        assert predicate(result.screen)
        return result

    def wait_assistant(self, *, predicate=lambda screen: True):
        assert predicate(self.selected.screen)
        return self.selected

    def filter_assistants(self, current, damage_type):
        self.calls.append(('filter', damage_type))
        return current

    def _survey_assistants(self, current, damage_type, remaining):
        assert 'student-1' not in {member.student_id for member in remaining}
        self.calls.append(('survey', damage_type))
        return current.screen.cards[0], current

    def important(self, kind, detail, **fields):
        self.calls.append((kind, fields))


def test_fallback_removes_actual_least_damage_slot_and_returns_unqualified_assistant_team(monkeypatch):
    from ba_automator import assault_assistant
    runner = FallbackRunner()
    damage = {member.student_id: 100 if member.slot != 1 else 0 for member in team()}
    observation = MockResult(True, 10, None, damage)
    monkeypatch.setattr(assault_assistant, 'decode_frame', lambda png: 'decoded')
    monkeypatch.setattr(assault_assistant, 'verify_assistant_preview', lambda image, words, selected, slot: slot == 1)
    result, selected = runner.choose_assistant_team(frame(SimpleNamespace(mock_target=(634, 574))), team(), observation)
    assert result is runner.final and selected == borrowed_team()
    assert runner.context_checked and runner.assistant == selected[1]
    assert runner.taps == [(634, 574), (1205, 182), (158, 595), (1083, 156), (628, 300), (1168, 593)]
    assert runner.calls[:2] == [('filter', 'mystic'), ('survey', 'mystic')]
    # No entry, Mobilize, fee confirmation, or proof API exists in this flow.
    assert runner.calls[-1][0] == 'total_assault_assistant'


def test_fallback_cannot_treat_missing_damage_as_zero():
    runner = FallbackRunner()
    with pytest.raises(RuntimeError, match='Mock damage report is incomplete'):
        runner.choose_assistant_team(None, team(), MockResult(False, None, None, {}))
    assert not runner.taps and not runner.context_checked
