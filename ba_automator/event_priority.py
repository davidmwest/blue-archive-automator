"""Calendar and opt-in AP priority for reviewed event profiles."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
import json

# Reviewed season profiles, one per event run. Their play periods never overlap.
PROFILE_FILES = ('aquatic-showdown.json', 'calm-after-the-storm.json')


@lru_cache(maxsize=1)
def profiles():
    root = files('ba_automator').joinpath('assets')
    return tuple(json.loads(root.joinpath(name).read_text(encoding="utf-8"))
                 for name in PROFILE_FILES)


def available_event(now):
    if now.tzinfo is None:
        raise ValueError('Event availability requires an aware timestamp')
    moment = now.astimezone(timezone.utc)
    for value in profiles():
        start = datetime.fromisoformat(value['starts_at'])
        end = datetime.fromisoformat(value['playable_until'])
        if start <= moment < end:
            return deepcopy(value)  # callers adjust their copy; the reviewed profile stays intact
    return None


def active_event(config, now):
    return available_event(now) if config.ap_event_priority else None
