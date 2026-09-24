"""Durable paid-purchase intent; an uncertain charge is never retried."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from uuid import uuid4


PACKS = {
    'monthly': ('Monthly Pyroxene Pack', 699),
    'half_monthly': ('Half Monthly Pyroxene Pack', 299),
    'ap': ('2-Week AP Pack', 299),
}


def enabled(config):
    return tuple(key for key in PACKS if getattr(config, f'packs_{key}_enabled'))


def state_path(config):
    identity = hashlib.sha256(f'{config.serial}\0{config.package}'.encode()).hexdigest()[:24]
    return config.state_dir / f'packs-{identity}.json'


def read_state(config):
    try:
        value = json.loads(state_path(config).read_text())
    except FileNotFoundError:
        return {'version': 1, 'pending': None, 'blocked_reason': None, 'next_check_at': None, 'observed': {}}
    except (OSError, ValueError) as exc:
        raise RuntimeError('Cannot read paid-pack state; inspect it before allowing purchases') from exc
    if (not isinstance(value, dict) or value.get('version') != 1
            or not {'pending', 'blocked_reason', 'next_check_at', 'observed'} <= value.keys()
            or not isinstance(value['observed'], dict)
            or value['blocked_reason'] is not None and not isinstance(value['blocked_reason'], str)):
        raise RuntimeError('Invalid paid-pack state; purchases are blocked')
    pending = value['pending']
    if pending is not None and (not isinstance(pending, dict) or pending.get('pack') not in PACKS
                               or type(pending.get('cents')) is not int or pending['cents'] <= 0):
        raise RuntimeError('Invalid pending purchase; purchases are blocked')
    if value['next_check_at'] is not None:
        try:
            stamp = datetime.fromisoformat(value['next_check_at'])
            if stamp.tzinfo is None:
                raise ValueError('Missing timezone')
        except (ValueError, TypeError) as exc:
            raise RuntimeError('Invalid paid-pack schedule; purchases are blocked') from exc
    return value


def write_state(config, state):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('w') as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def next_reset(now):
    now = now.astimezone(timezone.utc)
    reset = now.replace(hour=19, minute=1, second=0, microsecond=0)
    return (reset if now < reset else reset + timedelta(days=1)).isoformat()
