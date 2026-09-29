"""Event AP reservation. Unknown progress never falls through to ordinary farming."""
from datetime import datetime, timezone
from importlib.resources import files
import json


def active_event(config, now):
    if not config.ap_event_priority:
        return None
    if now.tzinfo is None:
        raise ValueError('Event availability requires an aware timestamp')
    value = json.loads(files('ba_automator').joinpath('assets/aquatic-showdown.json').read_text())
    start = datetime.fromisoformat(value['starts_at'])
    end = datetime.fromisoformat(value['playable_until'])
    return value if start <= now.astimezone(timezone.utc) < end else None


def priority_summary(profile, observation=None):
    prefix = f"Event AP reserved for {profile['title']} through Treasure Hunt round 3. "
    if observation == 'needs_first_clears':
        return prefix + 'Needs setup: clear event quests with three stars before farming. Normal AP farming is on hold.'
    return prefix + 'Event spending is not ready; normal AP farming is on hold. See the event setup panel.'
