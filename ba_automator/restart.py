"""Bounded startup state machine for the explicitly selected Blue Archive app."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Callable
from uuid import uuid4

from .config import Config
from .locking import InstanceLock
from .runtime import Capture, FRAME_MAX_AGE, HOME_STABLE_SECONDS, TRACE_LIMIT
from .runtime import Journal, RunResult, TaskError

# Compatibility for existing task callers and saved integrations.
_Journal = Journal
RestartError = TaskError


LOGGER = logging.getLogger(__name__)
MAX_REPEATED_ACTIONS = 5
MAX_ACTIONS = 40
ACTION_STATES = {"title", "download_prompt", "popup"}
DOWNLOAD_STATES = {"download_prompt", "downloading"}
KNOWN_STATES = ACTION_STATES | DOWNLOAD_STATES | {"home", "loading", "blocked", "unknown"}


class _Budget:
    def __init__(self, config: Config, started: float):
        self.config = config
        self.started = started
        self.accounted_at = started
        self.excluded_download = 0.0
        self.download_active = False
        self.download_deadline: float | None = None

    def advance(self, now: float) -> None:
        if self.download_active and self.download_deadline is not None:
            self.excluded_download += max(0.0, min(now, self.download_deadline) - self.accounted_at)
        self.accounted_at = now

    def observe(self, state: str, now: float) -> None:
        self.download_active = state in DOWNLOAD_STATES
        if self.download_active and self.download_deadline is None:
            # Only the first download opens a window. A later prompt never extends it.
            self.download_deadline = now + self.config.download_timeout

    def error(self, now: float) -> str | None:
        elapsed = now - self.started
        if elapsed >= self.config.startup_timeout + self.config.download_timeout:
            return "Restart exceeded its overall time limit"
        if (self.download_active and self.download_deadline is not None
                and now >= self.download_deadline):
            return "Game-data download exceeded its time limit"
        if elapsed - self.excluded_download >= self.config.startup_timeout:
            return "Blue Archive did not reach a stable home screen before the startup time limit"
        return None


def run_restart(
    config: Config,
    device,
    vision,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RunResult:
    """Restart the game and return only after its home screen remains unobstructed.

    The device and vision interfaces are deliberately injectable for screenshot replay
    and deterministic tests. Ctrl-C is journaled and propagated after releasing the lock.
    """
    started = monotonic()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = config.run_dir / f"restart-{stamp}-{uuid4().hex[:8]}"
    journal: _Journal | None = None
    actions = 0
    last_frame: str | None = None
    budget = _Budget(config, started)

    def fail(message: str) -> None:
        raise RestartError(message, run_dir)

    def check_deadlines() -> float:
        now = monotonic()
        budget.advance(now)
        error = budget.error(now)
        if error:
            fail(error)
        return now

    def operation(name: str, callback, **fields):
        assert journal is not None
        journal.record("intent", operation=name, **fields)
        LOGGER.info("restart: %s", name)
        try:
            result = callback()
        except BaseException as exc:
            journal.record("outcome", operation=name,
                           result="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed")
            raise
        journal.record("outcome", operation=name, result="skipped_stale" if result is False else "ok")
        return result

    try:
        journal = _Journal(run_dir, monotonic, started)
        journal.record("started", task="restart", serial=config.serial, package=config.package)
        with InstanceLock(config):
            device.connect()
            device.verify_package()
            size = device.display_size()
            if size != (config.expected_width, config.expected_height):
                fail(f"Expected a 1280×720 game display; selected instance reports {size[0]}×{size[1]}")
            check_deadlines()
            operation("force_stop", device.force_stop)
            check_deadlines()
            operation("launch", device.launch)

            previous_state: str | None = None
            repeated_actions: dict[tuple[str, tuple[int, int]], int] = {}
            home_since: float | None = None
            home_count = 0
            unknown_since: float | None = None
            next_action_at = started
            pending_popup: dict | None = None

            while True:
                captured_at = check_deadlines()
                png = device.screenshot()
                last_frame = journal.screenshot(png)
                observation = vision.analyze(png)
                foreground = device.foreground_package()
                capture = Capture(png, captured_at, foreground)
                now = check_deadlines()
                if foreground != config.package:
                    fail("Expected Blue Archive in the foreground; "
                         f"found {foreground or 'no identifiable app'}. No input was sent")

                if pending_popup is not None:
                    evidence, previous_target = pending_popup
                    after = f"popups/{evidence['id']}-after.png"
                    journal.save_image(after, png)
                    unchanged = (observation.state == "popup"
                                 and observation.detail == evidence["detail"]
                                 and observation.target == previous_target)
                    journal.record("popup_dismissal", **{
                        **evidence, "after": after, "after_state": observation.state,
                        "result": "unchanged" if unchanged else "changed",
                    })
                    pending_popup = None

                state = observation.state
                if state not in KNOWN_STATES:
                    fail(f"Vision returned an unsupported state: {state!r}")
                stale = not capture.is_fresh(now)
                if stale:
                    # Recapture instead of acting on slow OCR or a slow foreground query.
                    state = "unknown"
                budget.observe(state, now)
                error = budget.error(now)
                if error:
                    fail(error)

                if state != previous_state:
                    detail = "Frame expired before verification; recapturing" if stale else observation.detail
                    journal.record("state", state=state, detail=detail[:240], frame=last_frame)
                    LOGGER.info("restart: %s", state)
                    repeated_actions.clear()
                    previous_state = state

                if state == "blocked":
                    fail(f"Blue Archive needs attention: {observation.detail}")

                if state == "unknown":
                    if unknown_since is None:
                        unknown_since = now
                    if now - unknown_since >= config.unknown_timeout:
                        fail("No recognized startup screen appeared before the unknown-screen time limit; "
                             "review the local screenshot trace")
                else:
                    unknown_since = None

                if state == "home":
                    if home_since is None:
                        home_since = now
                    home_count += 1
                    if home_count >= config.home_confirmations and now - home_since >= HOME_STABLE_SECONDS:
                        journal.save_image("home.png", png)
                        duration = monotonic() - started
                        journal.record("finished", status="success", actions=actions, frame="home.png")
                        return RunResult("success", run_dir, duration, actions)
                else:
                    home_since = None
                    home_count = 0

                if state == "download_prompt" and not config.auto_download:
                    fail("Game-data download requires approval because auto_download is disabled")

                if state in ACTION_STATES and now >= next_action_at:
                    target = observation.target
                    if (not isinstance(target, tuple) or len(target) != 2
                            or any(type(value) is not int for value in target)
                            or not 0 <= target[0] < config.expected_width
                            or not 0 <= target[1] < config.expected_height):
                        fail(f"Recognized {state} screen has no valid, explicit tap target")
                    key = (state, target)
                    if repeated_actions.get(key, 0) >= MAX_REPEATED_ACTIONS:
                        fail(f"The {state} screen did not change after {MAX_REPEATED_ACTIONS} attempts")
                    if actions >= MAX_ACTIONS:
                        fail(f"Restart reached its limit of {MAX_ACTIONS} taps")

                    def verified_tap() -> bool:
                        # Intent persistence can take time on a busy disk; include it in freshness.
                        if not capture.is_fresh(monotonic()):
                            return False
                        # Transport rechecks after its shared-server preflight, which can block.
                        return device.tap(
                            *target, deadline=capture.deadline, monotonic=monotonic,
                        )

                    evidence = None
                    if state == "popup":
                        popup_id = uuid4().hex
                        before = f"popups/{popup_id}-before.png"
                        journal.save_image(before, png)
                        evidence = {
                            "id": popup_id, "detail": observation.detail[:240],
                            "detector": getattr(observation, "detector", "known"),
                            "time": datetime.now(timezone.utc).isoformat(),
                            "before": before, "after": None, "result": "pending",
                        }
                    if operation("tap", verified_tap, state=state, target=list(target), frame=last_frame):
                        actions += 1
                        repeated_actions[key] = repeated_actions.get(key, 0) + 1
                        next_action_at = monotonic() + config.action_cooldown
                        if evidence is not None:
                            journal.record("popup_dismissal", **evidence)
                            pending_popup = (evidence, target)
                    elif evidence is not None:
                        # A stale frame is not a dismissal attempt.
                        (run_dir / evidence["before"]).unlink(missing_ok=True)

                sleep(config.poll_interval)
    except KeyboardInterrupt:
        if journal is not None:
            journal.record("finished", status="interrupted", actions=actions, frame=last_frame)
        raise
    except Exception as exc:
        if journal is not None:
            journal.record("finished", status="failed", actions=actions,
                           reason=str(exc)[:1000], frame=last_frame)
        if isinstance(exc, RestartError):
            raise
        raise RestartError(f"Restart stopped: {exc}", run_dir) from exc
    finally:
        if journal is not None:
            journal.close()
