"""Club attendance contract; game input is deferred until a fresh-reset test."""

from datetime import datetime, timedelta, timezone
import logging
import time
from uuid import uuid4

from .runtime import Journal, RunResult


LOGGER = logging.getLogger(__name__)
RESET_HOUR_UTC = 19
DEFERRED_REASON = "Club check-in is stubbed; awaiting a fresh daily-reset test"


def game_day(now: datetime) -> str:
    """Global attendance resets at 19:00 UTC, independent of local DST."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Club game day requires a timezone-aware timestamp")
    return (now.astimezone(timezone.utc) - timedelta(hours=RESET_HOUR_UTC)).date().isoformat()


def run_club(config, device=None, vision=None, *, monotonic=time.monotonic,
             wall_clock=lambda: datetime.now(timezone.utc)) -> RunResult:
    """Reserve the daily step without input or a false attendance checkpoint.

    The eventual handler must verify Social -> Club, retain attendance evidence,
    return home, and persist one occurrence per configured instance/game day.
    Merely entering Club is not proof that a new 10 AP reward was delivered.
    """
    started = monotonic()
    now = wall_clock()
    day = game_day(now)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = config.run_dir / f"club-{stamp}-{uuid4().hex[:8]}"
    journal = Journal(run_dir, monotonic, started)
    try:
        journal.record("started", task="club", game_day=day)
        LOGGER.info("club: %s", DEFERRED_REASON)
        journal.record("finished", status="deferred", reason=DEFERRED_REASON,
                       actions=0, game_day=day, attendance_recorded=False)
        return RunResult("deferred", run_dir, monotonic() - started, 0)
    finally:
        journal.close()
