"""Validated, machine-local configuration for tasks and the dashboard."""

from __future__ import annotations

from dataclasses import dataclass, fields
import ipaddress
import math
from pathlib import Path
import re
import tomllib


class ConfigError(RuntimeError):
    """The supplied configuration cannot safely identify or run a device."""


@dataclass(frozen=True)
class Config:
    serial: str
    package: str
    adb_path: str = "adb"
    poll_interval: float = 1.5
    startup_timeout: float = 300.0
    download_timeout: float = 1800.0
    unknown_timeout: float = 60.0
    home_confirmations: int = 3
    action_cooldown: float = 3.0
    run_dir: Path = Path("data/runs")
    lock_dir: Path = Path("data/locks")
    state_dir: Path = Path("data/state")
    daily_schedule_enabled: bool = False
    daily_reset_delay_minutes: int = 1
    checkin_schedule_enabled: bool = True
    checkin_interval_minutes: int = 30
    cafe_schedule_enabled: bool = False
    cafe_invite_enabled: bool = False
    cafe_invite_student: str = ""
    crafting_schedule_enabled: bool = False
    ap_schedule_enabled: bool = False
    ap_floor: int = 100
    ap_strategy: str = 'elephs'
    ap_hard_default_order: bool = True
    ap_hard_order: tuple[str, ...] = ()
    packs_monthly_enabled: bool = False
    packs_half_monthly_enabled: bool = False
    packs_ap_enabled: bool = False
    packs_monthly_max_cents: int = 699
    packs_half_monthly_max_cents: int = 299
    packs_ap_max_cents: int = 299
    bounties_enabled_in_daily: bool = True
    scrimmages_enabled_in_daily: bool = True
    tactical_battles_enabled_in_daily: bool = True
    tactical_battles_skip_battles: bool = True
    tactical_battles_preserve_tickets: int = 1
    tactical_battles_refresh_limit: int = 50
    tactical_battles_confidence_percent: float = 99.0
    total_assault_difficulty: str = "hardcore"
    total_assault_enabled_in_daily: bool = False
    total_assault_comfort_seconds: int = 30
    lessons_strategy: str = "relationship"
    lessons_max_tickets: int = 0
    lessons_locations: tuple[str, ...] = ()
    lessons_enabled_in_daily: bool = True
    close_app_when_idle: bool = False
    auto_download: bool = True
    expected_width: int = 1280
    expected_height: int = 720

    def __post_init__(self) -> None:
        if not isinstance(self.serial, str) or not self.serial:
            raise ConfigError("device.serial must be an explicit loopback ADB endpoint")
        host, separator, port = self.serial.rpartition(":")
        try:
            address = ipaddress.ip_address(host.strip("[]")) if host != "localhost" else None
        except ValueError as exc:
            raise ConfigError("device.serial must use a loopback address and explicit port") from exc
        if (not separator or (host != "localhost" and not address.is_loopback)
                or not port.isascii() or not port.isdecimal() or len(port) > 5
                or not 1 <= int(port) <= 65535):
            raise ConfigError("device.serial must use a loopback address and port 1–65535")
        if ":" in host and not (host.startswith("[") and host.endswith("]")):
            raise ConfigError("IPv6 device.serial addresses must use [address]:port")
        # Canonicalize the common aliases so they cannot bypass the instance lock.
        canonical_host = "127.0.0.1" if host == "localhost" else str(address)
        if ":" in canonical_host:
            canonical_host = f"[{canonical_host}]"
        object.__setattr__(self, "serial", f"{canonical_host}:{int(port)}")
        if (not isinstance(self.package, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", self.package)
                or not any(part.lower() == "bluearchive" for part in self.package.split("."))):
            raise ConfigError("device.package must be a valid Blue Archive Android package name")
        if (not isinstance(self.adb_path, str) or not self.adb_path.strip()
                or any(character in self.adb_path for character in "\x00\r\n")):
            raise ConfigError("device.adb_path must be a nonempty executable name or path")
        limits = {
            "poll_interval": (0.1, 30),
            "startup_timeout": (5, 3600),
            "download_timeout": (5, 14400),
            "unknown_timeout": (5, 1800),
            "action_cooldown": (0.1, 30),
        }
        for name, (minimum, maximum) in limits.items():
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not minimum <= value <= maximum):
                raise ConfigError(f"restart.{name} must be a number from {minimum} to {maximum}")
        if type(self.home_confirmations) is not int or not 1 <= self.home_confirmations <= 10:
            raise ConfigError("restart.home_confirmations must be an integer from 1 to 10")
        if type(self.auto_download) is not bool:
            raise ConfigError("restart.auto_download must be true or false")
        for name in ("daily_schedule_enabled", "checkin_schedule_enabled", "cafe_schedule_enabled", "cafe_invite_enabled", "close_app_when_idle",
                     "lessons_enabled_in_daily", "bounties_enabled_in_daily", "scrimmages_enabled_in_daily",
                     "total_assault_enabled_in_daily", "tactical_battles_enabled_in_daily",
                     "tactical_battles_skip_battles", "crafting_schedule_enabled",
                     "packs_monthly_enabled", "packs_half_monthly_enabled", "packs_ap_enabled",
                     "ap_schedule_enabled", "ap_hard_default_order"):
            if type(getattr(self, name)) is not bool:
                raise ConfigError(f"{name} must be true or false")
        if (type(self.daily_reset_delay_minutes) is not int
                or not 0 <= self.daily_reset_delay_minutes <= 120):
            raise ConfigError("daily.reset_delay_minutes must be an integer from 0 to 120")
        if (type(self.checkin_interval_minutes) is not int
                or not 5 <= self.checkin_interval_minutes <= 1440):
            raise ConfigError("checkin.interval_minutes must be an integer from 5 to 1440")
        for name in ('packs_monthly_max_cents', 'packs_half_monthly_max_cents', 'packs_ap_max_cents'):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= 10000:
                raise ConfigError(f'{name} must be an integer from 1 to 10000 USD cents')
        from .ap_policy import STRATEGIES, stage_key
        if type(self.ap_floor) is not int or not 0 <= self.ap_floor <= 9999:
            raise ConfigError('ap.floor must be an integer from 0 to 9999')
        if self.ap_strategy not in STRATEGIES:
            raise ConfigError('ap.strategy must be elephs, reports, or credits')
        try:
            if not isinstance(self.ap_hard_order, (list, tuple)) or len(self.ap_hard_order) > 297:
                raise ValueError('Hard rotation must be a list of up to 297 stages')
            for stage in self.ap_hard_order: stage_key(stage)
            if len(set(self.ap_hard_order)) != len(self.ap_hard_order):
                raise ValueError('Hard rotation must not repeat a stage')
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        object.__setattr__(self, 'ap_hard_order', tuple(self.ap_hard_order))
        if (not isinstance(self.cafe_invite_student, str) or len(self.cafe_invite_student) > 100
                or any(ord(c) < 32 for c in self.cafe_invite_student)):
            raise ConfigError("cafe.invite_student must be a single student name")
        object.__setattr__(self, "cafe_invite_student", self.cafe_invite_student.strip())
        if (self.cafe_invite_enabled and self.cafe_invite_student
                and not re.search(r"[A-Za-z]", self.cafe_invite_student)):
            raise ConfigError("cafe.invite_student must use the English in-game name")
        if (type(self.tactical_battles_preserve_tickets) is not int
                or not 0 <= self.tactical_battles_preserve_tickets <= 5):
            raise ConfigError("tactical_battles.preserve_tickets must be an integer from 0 to 5")
        if (type(self.tactical_battles_refresh_limit) is not int
                or not 2 <= self.tactical_battles_refresh_limit <= 100):
            raise ConfigError("tactical_battles.refresh_limit must be an integer from 2 to 100")
        if (type(self.tactical_battles_confidence_percent) not in (int, float)
                or not 80 <= self.tactical_battles_confidence_percent <= 99.9):
            raise ConfigError("tactical_battles.confidence_percent must be a number from 80 to 99.9")
        if self.total_assault_difficulty not in ("normal", "hard", "very_hard", "hardcore", "extreme", "insane", "torment", "lunatic"):
            raise ConfigError("total_assault.difficulty must be normal, hard, very_hard, hardcore, extreme, insane, torment, or lunatic")
        if (type(self.total_assault_comfort_seconds) is not int
                or not 0 <= self.total_assault_comfort_seconds <= 300):
            raise ConfigError("total_assault.comfort_seconds must be an integer from 0 to 300")
        if self.lessons_strategy not in ("relationship", "school_rank"):
            raise ConfigError("lessons.strategy must be relationship or school_rank")
        if type(self.lessons_max_tickets) is not int or not 0 <= self.lessons_max_tickets <= 99:
            raise ConfigError("lessons.max_tickets must be an integer from 0 to 99 (0 uses available tickets)")
        if (not isinstance(self.lessons_locations, (list, tuple)) or len(self.lessons_locations) > 50
                or any(not isinstance(name, str) or not name.strip() or len(name) > 100
                       or any(ord(c) < 32 for c in name) for name in self.lessons_locations)):
            raise ConfigError("lessons.locations must be a list of up to 50 nonempty location names")
        locations = tuple(name.strip() for name in self.lessons_locations)
        if len({name.casefold() for name in locations}) != len(locations):
            raise ConfigError("lessons.locations must not contain duplicate names")
        object.__setattr__(self, "lessons_locations", locations)
        if (type(self.expected_width) is not int or self.expected_width != 1280
                or type(self.expected_height) is not int or self.expected_height != 720):
            raise ConfigError("The supported display is fixed at 1280×720")
        for name in ("run_dir", "lock_dir", "state_dir"):
            value = getattr(self, name)
            if (not isinstance(value, (str, Path)) or not str(value).strip()
                    or "\x00" in str(value)):
                raise ConfigError(f"storage.{name} must be a nonempty filesystem path")
            object.__setattr__(self, name, Path(value).expanduser())

    @classmethod
    def from_file(cls, path: str | Path) -> Config:
        """Read TOML; executable/storage paths are relative to the config file."""
        source = Path(path).expanduser().resolve()
        try:
            with source.open("rb") as stream:
                document = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Cannot read configuration {source}: {exc}") from exc
        allowed = {
            "device": {"serial", "package", "adb_path"},
            "restart": {"poll_interval", "startup_timeout", "download_timeout", "unknown_timeout",
                        "home_confirmations", "action_cooldown", "auto_download"},
            "storage": {"run_dir", "lock_dir", "state_dir"},
            "daily": {"schedule_enabled", "reset_delay_minutes"},
            "checkin": {"schedule_enabled", "interval_minutes"},
            "cafe": {"schedule_enabled", "invite_enabled", "invite_student"},
            "crafting": {"schedule_enabled"},
            "ap": {"schedule_enabled", "floor", "strategy", "hard_default_order", "hard_order"},
            "packs": {"monthly_enabled", "half_monthly_enabled", "ap_enabled",
                      "monthly_max_cents", "half_monthly_max_cents", "ap_max_cents"},
            "bounties": {"enabled_in_daily"},
            "scrimmages": {"enabled_in_daily"},
            "tactical_battles": {"enabled_in_daily", "skip_battles", "preserve_tickets", "refresh_limit", "confidence_percent"},
            "total_assault": {"difficulty", "enabled_in_daily", "comfort_seconds"},
            "lessons": {"strategy", "max_tickets", "locations", "enabled_in_daily"},
            "automation": {"close_app_when_idle"},
        }
        unexpected_sections = document.keys() - allowed.keys()
        if unexpected_sections:
            raise ConfigError(f"Unknown configuration sections: {', '.join(sorted(unexpected_sections))}")
        values: dict = {}
        for section, keys in allowed.items():
            entries = document.get(section, {})
            if not isinstance(entries, dict):
                raise ConfigError(f"{section} must be a TOML table")
            unexpected_keys = entries.keys() - keys
            if unexpected_keys:
                raise ConfigError(f"Unknown {section} settings: {', '.join(sorted(unexpected_keys))}")
            values.update({f"{section}_{key}" if section in {"daily", "checkin", "cafe", "lessons", "crafting", "packs", "ap", "bounties", "scrimmages", "total_assault", "tactical_battles"} else key: value
                           for key, value in entries.items()})
        for required in ("serial", "package"):
            if required not in values:
                raise ConfigError(f"device.{required} is required")
        config = cls(**values)
        resolved = {field.name: getattr(config, field.name) for field in fields(config)}
        for name in ("run_dir", "lock_dir", "state_dir"):
            resolved[name] = (source.parent / getattr(config, name)).resolve()
        adb = config.adb_path
        if "/" in adb or "\\" in adb or adb.startswith("~"):
            resolved["adb_path"] = str((source.parent / Path(adb).expanduser()).resolve())
        return cls(**resolved)
