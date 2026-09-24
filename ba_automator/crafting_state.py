"""Durable, per-instance crafting timers; the dashboard only reads these records."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from uuid import uuid4


class CraftStateError(RuntimeError):
    pass


def state_path(config):
    identity = hashlib.sha256(f'{config.serial}\0{config.package}'.encode()).hexdigest()[:24]
    return config.state_dir / f'crafting-{identity}.json'


def empty_state():
    return {'version': 1, 'slots': [], 'next_check_at': None, 'disabled_reason': None,
            'pending_action': None, 'updated_at': None}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('A crafting deadline must include its timezone')
    return parsed.astimezone(timezone.utc)


def read_state(config):
    try:
        value = json.loads(state_path(config).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        raise CraftStateError('Crafting state could not be read; automatic crafting is paused') from exc
    try:
        if not isinstance(value, dict) or value.get('version') != 1:
            raise ValueError('Unsupported state')
        if not isinstance(value.get('slots'), list) or len(value['slots']) > 3:
            raise ValueError('Invalid slots')
        seen = set()
        for slot in value['slots']:
            if type(slot['slot']) is not int or slot['slot'] not in (1, 2, 3) or slot['slot'] in seen:
                raise ValueError('Invalid slot identity')
            timestamp(slot['due_at'])
            seen.add(slot['slot'])
        if value.get('next_check_at') is not None:
            timestamp(value['next_check_at'])
        if value.get('disabled_reason') is not None and not isinstance(value['disabled_reason'], str):
            raise ValueError('Invalid disabled reason')
        pending = value.get('pending_action')
        if pending is not None and (not isinstance(pending, dict) or pending.get('kind') not in {'start', 'collect'}):
            raise ValueError('Invalid pending action')
        return {**empty_state(), **value}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CraftStateError('Crafting state is invalid; automatic crafting is paused') from exc


def write_state(config, value):
    """Caller holds the instance lock; replacement is atomic for concurrent readers."""
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def scheduled_jobs(state):
    if state['disabled_reason']:
        return []
    jobs = [{'task': 'crafting', 'slot': item['slot'], 'due_at': item['due_at'],
             'label': f"collect craft {item['slot']} + refill"} for item in state['slots']]
    if state['next_check_at']:
        jobs.append({'task': 'crafting', 'slot': None, 'due_at': state['next_check_at'],
                     'label': 'check for keystones'})
    return sorted(jobs, key=lambda item: timestamp(item['due_at']))
