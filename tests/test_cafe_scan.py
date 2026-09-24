"""Camera coverage cannot succeed from an unverified drag count."""
from types import SimpleNamespace

import pytest

import ba_automator.cafe as cafe_module
from ba_automator.cafe import CafeRunner, PAN_DOWN, PAN_LEFT, PAN_RIGHT, PAN_UP


def runner_with_motion(motions):
    runner = CafeRunner.__new__(CafeRunner)
    iterator = iter(motions)
    runner.pan = lambda vector: next(iterator)
    runner.fail = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    return runner


def test_overlapping_step_needs_actual_displacement():
    runner = runner_with_motion([(-100, 0), (-110, 0), (-140, 0), (-110, 0)])
    assert runner.pan_region(PAN_LEFT) is False


def test_boundary_needs_two_separately_stationary_drags():
    runner = runner_with_motion([(0, 0), (-80, 0), (0, 0), (0, 0)])
    assert runner.pan_region(PAN_LEFT, to_edge=True) is True


def test_unknown_motion_never_proves_boundary():
    runner = runner_with_motion([None] * 12)
    with pytest.raises(RuntimeError, match="could not be measured"):
        runner.pan_region(PAN_LEFT)


def test_unknown_motion_cannot_be_recovered_by_a_later_stationary_edge():
    runner = runner_with_motion([None, None, (0, 0), (0, 0)])
    with pytest.raises(RuntimeError, match="could not be measured"):
        runner.pan_region(PAN_LEFT)


def test_unknown_motion_between_stationary_frames_stops_coverage():
    runner = runner_with_motion([(0, 0), None, (0, 0), (0, 0)])
    with pytest.raises(RuntimeError, match="could not be measured"):
        runner.pan_region(PAN_LEFT)


@pytest.mark.parametrize("motion", [(100, 0), (-100, 70)])
def test_unexpected_motion_stops_scan(motion):
    with pytest.raises(RuntimeError, match="unexpectedly"):
        runner_with_motion([motion]).pan_region(PAN_LEFT)


def test_vertical_step_is_smaller_than_visible_scene_height():
    assert runner_with_motion([(0, -120), (0, -110)]).pan_region(PAN_UP) is False


def test_sweep_covers_short_final_row_and_records_only_after_all_boundaries(monkeypatch):
    runner = CafeRunner.__new__(CafeRunner)
    runner.floor, runner.config = 1, object()
    calls, actions = [], []
    runner.phase = lambda detail: None
    runner.pet_visible = lambda: calls.append("scan")
    # Initial corner, first horizontal row, down one short row, final row.
    outcomes = iter([True, True, False, True, True, False, True])
    runner.pan_region = lambda vector, **kw: (calls.append((vector, kw)), next(outcomes))[1]
    runner.fail = lambda detail: pytest.fail(detail)
    monkeypatch.setattr(cafe_module, "record_action", lambda *a, **kw: actions.append((a, kw)))
    runner.sweep()
    assert calls[1:3] == [(PAN_RIGHT, {"to_edge": True}), (PAN_DOWN, {"to_edge": True})]
    assert calls.count("scan") == 7
    assert [item[0][1] for item in actions] == ["cafe_scan_completed"]
    assert calls[-1] == "scan"


def test_sweep_does_not_report_completion_without_right_boundary(monkeypatch):
    runner = CafeRunner.__new__(CafeRunner)
    runner.floor, runner.config = 1, object()
    runner.phase = lambda detail: None
    runner.pet_visible = lambda: None
    runner.pan_region = lambda vector, **kw: bool(kw.get("to_edge"))
    runner.fail = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    actions = []
    monkeypatch.setattr(cafe_module, "record_action", lambda *a, **kw: actions.append(a))
    with pytest.raises(RuntimeError, match="width exceeded"):
        runner.sweep()
    assert actions == []
