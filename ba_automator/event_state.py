"""Durable, per-instance event progress and unreplayed battle intent."""
from .ap_state import state_path as ap_path
import json
import os
from uuid import uuid4


def state_path(config):
    return ap_path(config).with_name('event-' + ap_path(config).name[3:])


def read_state(config):
    try:
        value = json.loads(state_path(config).read_text())
    except FileNotFoundError:
        return dict(version=1, event_id=None, detected=False, declined=False,
                    pending=None, clears={}, stories={}, summary=None)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('Invalid event state; inspect before spending AP') from exc
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('clears'), dict)
            or type(value.get('detected')) is not bool
            or type(value.get('declined')) is not bool
            or 'event_id' not in value or 'summary' not in value or 'pending' not in value
            or value.get('pending') is not None and (
                not isinstance(value['pending'], dict) or not value['pending'])):
        raise RuntimeError('Invalid event state; inspect before spending AP')
    stories = value.setdefault('stories', {})
    if (not isinstance(stories, dict) or any(
            not isinstance(key, str) or not key.isdecimal()
            or not 1 <= int(key) <= 12 or cleared is not True
            for key, cleared in stories.items())):
        raise RuntimeError('Invalid event story progress; inspect before spending AP')
    return value


def write_state(config, state):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temp.open('w') as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def observe(config, profile):
    state = read_state(config)
    if state['event_id'] != profile['id']:
        if state['pending']:
            raise RuntimeError('An earlier event battle has an unresolved result')
        state.update(event_id=profile['id'], declined=False, clears={}, stories={}, summary=None)
    state['detected'] = True
    write_state(config, state)
    return state


def ensure_safe(config):
    if read_state(config)['pending']:
        raise RuntimeError('Event battle has an unresolved result; inspect its saved trace before restarting or spending AP')
