"""Pure Joint Firing Drill planning; observations authorize every transition."""
from dataclasses import asdict
import hashlib
import json
import math

POLICIES = ('inspect', 'sweep', 'clear_and_sweep')
PRACTICE_LIMIT = 6


def validate_plan(teams, stages):
    if len(teams) != 3 or len(stages) != 3:
        raise ValueError('Prepare three Joint Firing Drill teams and stages')
    used = set()
    borrowed = 0
    for team, stage in zip(teams, stages):
        if type(stage) is not int or not 1 <= stage <= 4:
            raise ValueError('Drill stages must be 1–4')
        if len(team) != 6 or {m.slot for m in team} != set(range(6)):
            raise ValueError('Each prepared Drill team needs six readable slots')
        own = set()
        variants = set()
        for m in team:
            identity = m.student_id.casefold().strip()
            if identity in variants or (not m.assistant and identity in used):
                raise ValueError(f'{m.student_id} is used in more than one Drill slot')
            if m.role != ('striker' if m.slot < 4 else 'special'):
                raise ValueError('Drill formation roles do not match their slots')
            variants.add(identity)
            if m.assistant:
                borrowed += 1
                if borrowed > 1:
                    raise ValueError('Only one assistant can be used across the three Drill rounds')
                if not isinstance(m.assistant_id, str) or not m.assistant_id.strip():
                    raise ValueError('The exact Drill assistant offering must be observed')
            else:
                own.add(identity)
        used.update(own)
    return True


def fingerprint(team, stage, skills):
    if not isinstance(skills, str) or not skills:
        raise ValueError('Starting skills must be observed')
    payload = {'stage': stage, 'team': [asdict(m) for m in sorted(team, key=lambda m: m.slot)],
               'skills': skills, 'policy': 'auto-v1'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def comfortable(won, remaining_seconds, margin):
    if type(margin) not in (int, float) or not math.isfinite(margin) or margin < 0:
        raise ValueError('Practice victory margin must be finite and nonnegative')
    return (won is True and type(remaining_seconds) in (int, float)
            and math.isfinite(remaining_seconds) and 0 <= remaining_seconds <= 180
            and remaining_seconds >= margin)


def sweep_count(tickets, reserve, today_score):
    if any(type(n) is not int or n < 0 for n in (tickets, reserve, today_score)):
        raise ValueError('Tickets, reserve, and today’s score must be observed')
    return max(0, tickets - reserve) if today_score > 0 else 0
