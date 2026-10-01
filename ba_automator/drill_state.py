"""Atomic Drill journal. Unresolved spending survives reset and process death."""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from uuid import uuid4

from .drill_policy import PRACTICE_LIMIT


class DrillStateError(RuntimeError):
    pass


def state_path(config):
    key = hashlib.sha256(f'{config.serial}\0{config.package}'.encode()).hexdigest()[:24]
    return config.state_dir / f'drill-{key}.json'


def empty_state():
    return dict(version=1, context=None, attempts=[], proofs={}, pending=None,
                last_summary=None, updated_at=None, plan=None)


def validate(s):
    if not isinstance(s, dict) or set(s) != set(empty_state()) or s['version'] != 1:
        raise ValueError('Invalid Drill state schema')
    if not isinstance(s['attempts'], list) or not isinstance(s['proofs'], dict):
        raise ValueError('Invalid Drill practice state')
    if len(s['attempts']) > PRACTICE_LIMIT:
        raise ValueError('Invalid Drill practice count')
    if s['context'] is not None and (not isinstance(s['context'], str) or not s['context']):
        raise ValueError('Invalid Drill context')
    for attempt in s['attempts']:
        if not isinstance(attempt, str) or not re.fullmatch(r'[0-9a-f]{64}', attempt):
            raise ValueError('Invalid Drill attempt fingerprint')
    # This is an attempt ledger, not a set: rebuilding an expired free room
    # can repeat a proven round, and every replay still consumes the daily budget.
    for key, proof in s['proofs'].items():
        if key not in s['attempts'] or not isinstance(proof, dict) or proof.get('won') is not True:
            raise ValueError('Invalid Drill proof')
        if type(proof.get('remaining')) not in (int, float) or not 0 <= proof['remaining'] <= 180:
            raise ValueError('Invalid Drill victory margin')
    if s['plan'] is not None:
        from .assault_policy import TeamMember
        from .drill_policy import validate_plan, fingerprint
        plan = s['plan']
        teams = [tuple(TeamMember(**m) for m in team) for team in plan['teams']]
        validate_plan(teams, plan['stages'])
        if not isinstance(plan['period'], str) or not plan['period']:
            raise ValueError('Missing Drill plan season')
        keys = [fingerprint(t, stage, skill) for t, stage, skill in zip(teams, plan['stages'], plan['skills'])]
        if len(keys) != 3 or keys != plan['fingerprints']:
            raise ValueError('Invalid Drill plan fingerprints')
    p = s['pending']
    if p is not None:
        if not isinstance(p, dict) or p.get('kind') not in ('entry', 'sweep'):
            raise ValueError('Invalid Drill spending intent')
        if (type(p.get('before')) is not int or type(p.get('count')) is not int
                or not 1 <= p['count'] <= p['before'] or (p['kind'] == 'entry' and p['count'] != 1)):
            raise ValueError('Invalid Drill ticket intent')
        if type(p.get('round')) is not int or not 0 <= p['round'] <= 3:
            raise ValueError('Invalid Drill entry progress')
        if not isinstance(p.get('context'), str) or not p['context']:
            raise ValueError('Missing Drill intent context')
        if not isinstance(p.get('id'), str) or not p['id']:
            raise ValueError('Missing Drill intent identity')
        if p['context'] != s['context']:
            raise ValueError('Drill intent belongs to another context')
        fingerprints = p.get('fingerprints')
        if (not isinstance(fingerprints, list) or
                (p['kind'] == 'entry' and (len(fingerprints) != 3
                 or len(set(fingerprints)) != 3 or any(k not in s['proofs'] for k in fingerprints)))):
            raise ValueError('Invalid Drill entry qualifications')
        if 'confirmation_sent' in p and (p['kind'] != 'sweep' or p['confirmation_sent'] is not True):
            raise ValueError('Invalid Drill sweep confirmation checkpoint')
        battle = p.get('battle')
        if battle is not None:
            if (p['kind'] != 'entry' or not isinstance(battle, dict)
                    or type(battle.get('index')) is not int
                    or not 0 <= battle['index'] < 3 or battle['index'] != p['round']
                    or type(battle.get('stage')) is not int or not 1 <= battle['stage'] <= 4
                    or battle.get('fingerprint') != fingerprints[battle['index']]
                    or type(battle.get('auto_verified')) is not bool):
                raise ValueError('Invalid Drill battle checkpoint')
        if 'result_unacknowledged' in p and p['result_unacknowledged'] is not True:
            raise ValueError('Invalid Drill result checkpoint')
        if 'receipt' in p and (not isinstance(p['receipt'], str) or not p['receipt']):
            raise ValueError('Invalid Drill receipt checkpoint')
    return s


def read_state(config):
    try:
        s = json.loads(state_path(config).read_text())
        if isinstance(s, dict):
            s.setdefault('plan', None)
        return validate(s)
    except FileNotFoundError:
        return empty_state()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise DrillStateError('Cannot verify Joint Firing Drill state; inspect it before spending') from exc


def write_state(config, state):
    validate(state)
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temp.open('w') as f:
            json.dump(state, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return state


def for_context(config, context):
    if not isinstance(context, str) or not context:
        raise DrillStateError('A verified day and season are required')
    s = read_state(config)
    if s['pending'] is not None and s['pending']['context'] != context:
        raise DrillStateError('Unresolved Drill entry from another day or season; reconcile before spending')
    if s['context'] != context:
        s['context'], s['attempts'], s['proofs'] = context, [], {}
        write_state(config, s)
    return s


def begin_practice(config, context, fingerprint):
    s = for_context(config, context)
    if s['pending'] is not None:
        raise DrillStateError('Resolve the paid Drill entry before practice')
    if fingerprint in s['attempts'] and fingerprint not in s['proofs']:
        raise DrillStateError('This Drill setup was already attempted today; change the plan or review its result')
    if len(s['attempts']) >= PRACTICE_LIMIT:
        raise DrillStateError('Daily limit of six Drill practice battles reached')
    s['attempts'].append(fingerprint)
    # A free room may expire between rounds. Replay a previously won round to
    # reach the unfinished teams, but require fresh evidence before paid entry.
    # Removing the proof durably also blocks replay after an unknown outcome.
    s['proofs'].pop(fingerprint, None)
    return write_state(config, s)


def record_practice(config, fingerprint, won, remaining, margin):
    from .drill_policy import comfortable
    s = read_state(config)
    if fingerprint not in s['attempts']:
        raise DrillStateError('Missing durable Drill practice intent')
    if comfortable(won, remaining, margin):
        s['proofs'][fingerprint] = dict(won=True, remaining=remaining)
    else:
        s['proofs'].pop(fingerprint, None)
    return write_state(config, s)


def qualified(config, fingerprints, margin):
    """Recheck the current margin; raising it invalidates weaker old proofs."""
    from .drill_policy import comfortable
    s = read_state(config)
    return (len(fingerprints) == 3 and len(set(fingerprints)) == 3
            and all(k in s['proofs'] and comfortable(True, s['proofs'][k]['remaining'], margin)
                    for k in fingerprints))


def begin_spend(config, context, before, count, *, kind, fingerprints=()):
    s = for_context(config, context)
    if s['pending'] is not None:
        raise DrillStateError('Previous Drill ticket confirmation is unresolved; it will not be repeated')
    if kind == 'entry' and (len(fingerprints) != 3 or len(set(fingerprints)) != 3
                            or any(k not in s['proofs'] for k in fingerprints)):
        raise DrillStateError('All three Drill teams need verified practice victories')
    s['pending'] = dict(id=uuid4().hex, kind=kind, context=context, before=before,
                        count=count, round=0, fingerprints=list(fingerprints),
                        created_at=datetime.now(timezone.utc).isoformat())
    return write_state(config, s)


def record_round(config, *, completed_rounds, score, evidence):
    """Record a positive result; the next round separately verifies menu progress."""
    s = read_state(config)
    p = s['pending']
    if (p is None or p['kind'] != 'entry' or type(completed_rounds) is not int
            or completed_rounds != p['round'] + 1 or not 1 <= completed_rounds <= 3
            or type(score) is not int or score <= 0 or not isinstance(evidence, str) or not evidence):
        raise DrillStateError('Drill round progress is unverified or out of order')
    p['round'] = completed_rounds
    p.pop('battle', None)
    p['result_unacknowledged'] = True
    p.setdefault('results', []).append(dict(round=completed_rounds, score=score, evidence=evidence))
    return write_state(config, s)


def finish_spend(config, tickets, *, receipt_verified):
    s = read_state(config)
    p = s['pending']
    if (p is None or tickets != p['before'] - p['count'] or not receipt_verified
            or (p['kind'] == 'entry' and p['round'] != 3)):
        raise DrillStateError('Drill ticket delta, completed rounds, and receipt do not agree')
    s['pending'] = None
    s['proofs'] = {}
    return write_state(config, s)


def ensure_restart_safe(config):
    if read_state(config)['pending'] is not None:
        raise DrillStateError('Joint Firing Drill has an unresolved entry or receipt; resume Drill before restarting')


def save_plan(config, period, teams, stages, skills):
    from dataclasses import asdict
    from .drill_policy import validate_plan, fingerprint
    validate_plan(teams, stages)
    if len(skills) != 3:
        raise DrillStateError('All three starting-skill plans must be observed')
    s = read_state(config)
    if s['pending'] is not None:
        raise DrillStateError('Cannot change a plan during a paid entry')
    s['plan'] = dict(period=period, teams=[[asdict(m) for m in t] for t in teams],
                     stages=list(stages), skills=list(skills),
                     fingerprints=[fingerprint(t, stage, skill) for t, stage, skill in zip(teams, stages, skills)])
    return write_state(config, s)


def begin_round(config, index, stage, key):
    """Save the exact qualified round before Mobilize can change the screen."""
    s = read_state(config)
    p = s['pending']
    if (type(index) is not int or not 0 <= index < 3
            or type(stage) is not int or not 1 <= stage <= 4
            or p is None or p['kind'] != 'entry' or p['round'] != index
            or key != p['fingerprints'][index] or p.get('battle') is not None):
        raise DrillStateError('Drill round intent conflicts with saved progress')
    p['battle'] = dict(index=index, stage=stage, fingerprint=key, auto_verified=False)
    return write_state(config, s)


def observe_auto(config):
    s = read_state(config)
    if s['pending'] and s['pending'].get('battle'):
        if not s['pending']['battle']['auto_verified']:
            s['pending']['battle']['auto_verified'] = True
            write_state(config, s)


def acknowledge_round(config):
    s = read_state(config)
    p = s['pending']
    if p is None or p['kind'] != 'entry' or not p.get('result_unacknowledged'):
        raise DrillStateError('No Drill result to acknowledge')
    p.pop('result_unacknowledged')
    return write_state(config, s)


def record_receipt(config, evidence):
    s = read_state(config)
    if s['pending'] is None or not evidence:
        raise DrillStateError('Missing Drill receipt intent or evidence')
    s['pending']['receipt'] = str(evidence)
    return write_state(config, s)
