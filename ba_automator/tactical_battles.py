"""Reserved battle entry point. Rewards are implemented separately."""

from datetime import datetime, timezone
import time
from uuid import uuid4
from .runtime import Journal, RunResult


def run_tactical_battles(config, device=None, vision=None, *, monotonic=time.monotonic):
    """No device access, ticket use, team changes, or attendance claim."""
    started = monotonic()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = config.run_dir / f"tactical_battles-{stamp}-{uuid4().hex[:8]}"
    journal = Journal(run_dir, monotonic, started)
    try:
        journal.record(
            "finished",
            status="deferred",
            actions=0,
            reason="Tactical Challenge battle rules are not implemented; no game input sent",
        )
        return RunResult("deferred", run_dir, monotonic() - started, 0)
    finally:
        journal.close()
