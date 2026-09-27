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


def sweep_refill(expected_ap, observed_ap, before_capacity, after_capacity):
    """Return a proven single-level refill (or zero); None means unresolved.

    A level-up raises AP capacity by two and awards the new capacity. Do not
    treat an arbitrary balance increase as regeneration or infer a refill
    from the balance alone. One regeneration tick is allowed at the receipt.
    Multiple level-ups stay unresolved until their individual rewards can be
    verified; they cannot be explained by the final capacity alone.
    """
    if any(type(v) is not int or v < 0 for v in (expected_ap, observed_ap)):
        return None
    if any(
        v is not None and (type(v) is not int or v <= 0)
        for v in (before_capacity, after_capacity)
    ):
        return None
    if (
        before_capacity is None or after_capacity is None
        or before_capacity == after_capacity
    ):
        return 0 if expected_ap <= observed_ap <= expected_ap + 1 else None
    if (
        after_capacity == before_capacity + 2
        and expected_ap + after_capacity <= observed_ap
        <= expected_ap + after_capacity + 1
    ):
        return after_capacity
    return None


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
