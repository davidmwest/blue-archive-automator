"""Opening celebration frames must not become the permanent reward receipt."""

import pytest

from ba_automator.relationship import RelationshipIncrease, RelationshipStat, wait_relationship_details


def settle(*results):
    class Clock:
        now = 0

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    observed = []
    pending = list(results)

    def capture():
        observed.append(clock.now)
        return pending.pop(0) if len(pending) > 1 else pending[0]

    result = wait_relationship_details(capture=capture, read=lambda frame: frame,
                                       clock=lambda: clock.now, sleep=clock.sleep)
    return result, observed, clock.now


def test_waits_for_animation_and_two_matching_reads_without_requiring_student_name():
    early = RelationshipIncrease(None, None, ())
    complete = RelationshipIncrease(None, 11, (RelationshipStat("ATK", delta=27),
                                               RelationshipStat("HP", delta=49)))
    (frame, result, settled), observed, elapsed = settle(early, complete, complete)
    assert result == frame == complete and settled
    assert observed[0] == 3
    assert elapsed == pytest.approx(4.2)


def test_changed_reading_must_stabilize_instead_of_combining_frames():
    first = RelationshipIncrease(None, 11, (RelationshipStat("ATK", delta=2),))
    second = RelationshipIncrease(None, 11, (RelationshipStat("ATK", delta=27),))
    (_, result, settled), observed, _ = settle(first, second, second)
    assert result == second and settled and len(observed) == 3


def test_unreadable_details_are_retained_after_bounded_wait():
    partial = RelationshipIncrease(None, None, (RelationshipStat("ATK", delta=27),))
    (_, result, settled), _, elapsed = settle(partial)
    assert result == partial and not settled
    assert elapsed == 8


def test_disappearing_celebration_does_not_authorize_dismissal():
    with pytest.raises(ValueError, match="celebration changed"):
        settle(RelationshipIncrease(None, None, ()), None)
