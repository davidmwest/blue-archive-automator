"""Calendar and opt-in AP priority for reviewed event profiles."""
from datetime import datetime, timezone
from importlib.resources import files
import json


def available_event(now):
    if now.tzinfo is None:
        raise ValueError('Event availability requires an aware timestamp')
    value = json.loads(files('ba_automator').joinpath('assets/aquatic-showdown.json').read_text())
    start = datetime.fromisoformat(value['starts_at'])
    end = datetime.fromisoformat(value['playable_until'])
    return value if start <= now.astimezone(timezone.utc) < end else None


def active_event(config, now):
    return available_event(now) if config.ap_event_priority else None
