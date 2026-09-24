"""Daily ticket allocation and durable, unreplayed sweep intents."""

from datetime import date
import json
from .ap_state import state_path as ap_path
import os
from uuid import uuid4

AREAS = {
    "bounties": ("Overpass", "Desert Railroad", "Classroom"),
    "scrimmages": ("Trinity", "Gehenna", "Millennium"),
}


def allocation(tickets, day):
    if type(tickets) is not int or not 0 <= tickets <= 999:
        raise ValueError("Ticket count must be an integer from 0 to 999")
    start = (
        date.fromisoformat(day).weekday() % 3
    )  # Monday = 0; game day, not local midnight.
    result = [tickets // 3] * 3
    for extra in range(tickets % 3):
        result[(start + extra) % 3] += 1
    return result


def state_path(config, task):
    if task not in AREAS:
        raise ValueError("Unknown ticket task")
    return ap_path(config).with_name(ap_path(config).name.replace("ap-", f"{task}-", 1))


def empty_state():
    return dict(
        version=1, day=None, total=0, quotas=[0, 0, 0], done=[0, 0, 0], pending=None
    )


def read_state(config, task):
    try:
        state = json.loads(state_path(config, task).read_text())
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Cannot read ticket state; inspect it before spending"
        ) from exc
    try:
        if set(state) != set(empty_state()) or state["version"] != 1:
            raise ValueError()
        if state["day"] is None:
            if state != empty_state():
                raise ValueError()
        else:
            if state["quotas"] != allocation(state["total"], state["day"]):
                raise ValueError()
            if (
                not isinstance(state["done"], list)
                or len(state["done"]) != 3
                or any(
                    type(n) is not int or not 0 <= n <= q
                    for n, q in zip(state["done"], state["quotas"])
                )
            ):
                raise ValueError()
        pending = state["pending"]
        if pending is not None:
            if (
                not isinstance(pending, dict)
                or pending.get("area") not in AREAS[task]
                or any(
                    type(pending.get(k)) is not int or pending[k] < 0
                    for k in ("count", "tickets_before", "ap_before", "ap_cost")
                )
                or pending["count"] < 1
                or pending["count"] > pending["tickets_before"]
                or not isinstance(pending.get("stage"), str)
                or not isinstance(pending.get("run_dir"), str)
            ):
                raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("Invalid ticket state; spending is blocked") from exc
    return state


def write_state(config, task, state):
    path = state_path(config, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
