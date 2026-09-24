"""Per-instance stage survey, rotation cursor, and unreplayed AP spend intent."""

from datetime import datetime
import hashlib
import json
import os
import re
from uuid import uuid4
from .ap_policy import stage_key


def state_path(config):
    identity = hashlib.sha256(
        f"{config.serial}\0{config.package}".encode()
    ).hexdigest()[:24]
    return config.state_dir / f"ap-{identity}.json"


def empty_state():
    return {
        "version": 1,
        "hard_stages": [],
        "commissions": {},
        "surveyed_at": None,
        "next_stage": None,
        "pending": None,
        "blocked_reason": None,
        "next_check_at": None,
        "last_ap": None,
        "last_summary": None,
    }


def read_state(config):
    try:
        value = json.loads(state_path(config).read_text())
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cannot read AP state; inspect it before spending") from exc
    try:
        if (
            not isinstance(value, dict)
            or set(value) != set(empty_state())
            or value["version"] != 1
        ):
            raise ValueError()
        if (
            not isinstance(value["hard_stages"], list)
            or len(value["hard_stages"]) > 297
        ):
            raise ValueError()
        for stage in value["hard_stages"]:
            stage_key(stage)
        if len(set(value["hard_stages"])) != len(value["hard_stages"]):
            raise ValueError()
        if not isinstance(value["commissions"], dict) or set(value["commissions"]) - {
            "credits",
            "reports",
        }:
            raise ValueError()
        for stage in value["commissions"].values():
            if stage is not None and (
                not isinstance(stage, str) or not re.fullmatch("[A-Z]", stage)
            ):
                raise ValueError()
        if value["next_stage"] is not None:
            stage_key(value["next_stage"])
        for key in ("blocked_reason", "last_summary"):
            if value[key] is not None and not isinstance(value[key], str):
                raise ValueError()
        for key in ("surveyed_at", "next_check_at"):
            if (
                value[key] is not None
                and datetime.fromisoformat(value[key]).tzinfo is None
            ):
                raise ValueError()
        if value["last_ap"] is not None and (
            type(value["last_ap"]) is not int or value["last_ap"] < 0
        ):
            raise ValueError()
        pending = value["pending"]
        if pending is not None:
            if (
                not isinstance(pending, dict)
                or pending.get("strategy") not in ("elephs", "reports", "credits")
                or not isinstance(pending.get("stage"), str)
                or any(
                    type(pending.get(k)) is not int or pending[k] < 0
                    for k in ("ap_before", "cost", "count", "floor")
                )
                or pending["cost"] == 0
                or pending["count"] == 0
            ):
                raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("Invalid AP state; spending is blocked") from exc
    return value


def write_state(config, state):
    path = state_path(config)
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
