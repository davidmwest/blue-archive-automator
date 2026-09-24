"""Pure AP budgets and a durable, one-sweep-per-stage round robin."""

from dataclasses import dataclass
import re

STRATEGIES = ("elephs", "reports", "credits")


def stage_key(stage):
    if not isinstance(stage, str) or not re.fullmatch(r"[1-9]\d?-[123]", stage):
        raise ValueError("Hard stages must look like 13-3 (areas 1–99, stages 1–3)")
    return tuple(map(int, stage.split("-")))


def default_order(stages):
    return tuple(sorted(set(stages), key=stage_key, reverse=True))


def sweep_count(ap, floor, cost, limit=999):
    """Never round upward or treat missing AP/cost as zero."""
    if any(type(v) is not int for v in (ap, floor, cost, limit)):
        raise ValueError("AP, floor, sweep cost, and limit must be integers")
    if min(ap, floor, limit) < 0 or cost <= 0:
        raise ValueError("Invalid AP budget")
    return min(limit, max(0, (ap - floor) // cost))


@dataclass(frozen=True)
class RoundRobinChoice:
    stage: str
    next_stage: str


def choose_hard(order, next_stage, unavailable=()):
    """Skip exhausted/ineligible stages without charging to reset attempts."""
    if not order:
        return None
    start = order.index(next_stage) if next_stage in order else 0
    for offset in range(len(order)):
        index = (start + offset) % len(order)
        if order[index] not in unavailable:
            return RoundRobinChoice(order[index], order[(index + 1) % len(order)])
    return None
