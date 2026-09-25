"""Camera coverage cannot succeed from an unverified drag count."""
from types import SimpleNamespace

import pytest

import ba_automator.cafe as cafe_module
from ba_automator.cafe import CafeRunner, PAN_DOWN, PAN_LEFT, PAN_RIGHT, PAN_UP


def runner_with_motion(motions):
    runner = CafeRunner.__new__(CafeRunner)
    iterator = iter(motions)
    runner.motions_seen, runner.scanned_after = [], []
    runner.journal = SimpleNamespace(record=lambda *args, **kwargs: None)
    runner.last_frame = "after.png"

    def pan(vector):
        motion = next(iterator)
        runner.motions_seen.append(motion)
        return motion

    runner.pan = pan
    runner.pet_visible = lambda: runner.scanned_after.append(len(runner.motions_seen))
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
    with pytest.raises(RuntimeError, match="could not be verified after twelve drags"):
        runner.pan_region(PAN_LEFT)
    assert runner.scanned_after == list(range(1, 13))
    assert len(runner.motions_seen) == 12


def test_each_unknown_movement_is_scanned_before_a_later_verified_edge():
    runner = runner_with_motion([None, None, (0, 0), (0, 0)])
    assert runner.pan_region(PAN_LEFT) is True
    assert runner.scanned_after == [1, 2]
    assert len(runner.motions_seen) == 4


def test_unknown_motion_requires_two_new_stationary_frames_to_prove_edge():
    runner = runner_with_motion([(0, 0), None, (0, 0), (0, 0)])
    assert runner.pan_region(PAN_LEFT) is True
    assert runner.scanned_after == [2]
    assert len(runner.motions_seen) == 4


def test_unknown_motion_restarts_distance_from_its_newly_scanned_view():
    runner = runner_with_motion([(-200, 0), None, (-200, 0), (-200, 0), (-60, 0)])
    assert runner.pan_region(PAN_LEFT) is False
    assert runner.scanned_after == [2]
    assert len(runner.motions_seen) == 5


def test_failed_scan_after_unknown_motion_sends_no_further_drag():
    runner = runner_with_motion([None, (0, 0), (0, 0)])
    runner.pet_visible = lambda: runner.fail("Student view could not be verified")
    with pytest.raises(RuntimeError, match="Student view could not be verified"):
        runner.pan_region(PAN_LEFT)
    assert len(runner.motions_seen) == 1


@pytest.mark.parametrize("motion", [(100, 0), (-100, 70)])
def test_unexpected_motion_stops_scan(motion):
    with pytest.raises(RuntimeError, match="unexpectedly"):
        runner_with_motion([motion]).pan_region(PAN_LEFT)


def test_small_edge_perspective_drift_is_scanned_and_needs_new_boundary_proof():
    runner = runner_with_motion([(0, 0), (60, -31), (0, 0), (0, 0)])
    assert runner.pan_region(PAN_RIGHT, to_edge=True) is True
    assert runner.scanned_after == [2]
    assert len(runner.motions_seen) == 4


@pytest.mark.parametrize("motion", [(-60, -31), (60, -41), (0, 31)])
def test_edge_recovery_rejects_reverse_large_or_uncommanded_motion(motion):
    with pytest.raises(RuntimeError, match="unexpectedly"):
        runner_with_motion([motion]).pan_region(PAN_RIGHT, to_edge=True)


def test_edge_corrections_are_bounded_and_cannot_prove_camera_coverage():
    runner = runner_with_motion([(60, -31)] * 3)
    with pytest.raises(RuntimeError, match="unexpectedly"):
        runner.pan_region(PAN_RIGHT, to_edge=True)
    assert runner.scanned_after == [1, 2]


def test_small_perspective_drift_does_not_relax_normal_row_coverage():
    with pytest.raises(RuntimeError, match="unexpectedly"):
        runner_with_motion([(60, -31)]).pan_region(PAN_RIGHT)


def test_failed_scan_after_edge_correction_sends_no_more_input():
    runner = runner_with_motion([(60, -31), (0, 0), (0, 0)])
    runner.pet_visible = lambda: runner.fail("Student view could not be verified")
    with pytest.raises(RuntimeError, match="Student view could not be verified"):
        runner.pan_region(PAN_RIGHT, to_edge=True)
    assert len(runner.motions_seen) == 1


def test_vertical_step_is_smaller_than_visible_scene_height():
    assert runner_with_motion([(0, -120), (0, -110)]).pan_region(PAN_UP) is False


def test_unmeasured_pan_retries_frames_without_additional_input(monkeypatch):
    runner = CafeRunner.__new__(CafeRunner)
    runner.clock = lambda: 0
    runner.sleep = lambda seconds: None
    runner.last_frame = "after.png"
    runner.journal = SimpleNamespace(record=lambda *a, **kw: None)
    swipes = []
    runner.device = SimpleNamespace(swipe=lambda *a, **kw: (swipes.append(a), True)[1])
    frames = iter(["before", "animated", "settled"])
    runner.wait_cafe = lambda: (0, b"", next(frames), [])
    comparisons = []
    def measure(before, after, **kwargs):
        comparisons.append((before, after))
        return None if after == "animated" else (-100, 0)
    monkeypatch.setattr(cafe_module, "measure_camera_displacement", measure)
    assert runner.pan(PAN_LEFT) == (-100, 0)
    assert swipes == [(PAN_LEFT[0], PAN_LEFT[1])]
    assert comparisons == [("before", "animated"), ("before", "settled")]


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
