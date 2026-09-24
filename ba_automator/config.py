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
    cafe_schedule_enabled: bool = False
    cafe_invite_enabled: bool = False
    cafe_invite_student: str = ""
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
        for name in ("cafe_schedule_enabled", "cafe_invite_enabled", "close_app_when_idle",
                     "lessons_enabled_in_daily"):
            if type(getattr(self, name)) is not bool:
                raise ConfigError(f"{name} must be true or false")
        if (not isinstance(self.cafe_invite_student, str) or len(self.cafe_invite_student) > 100
                or any(ord(c) < 32 for c in self.cafe_invite_student)):
            raise ConfigError("cafe.invite_student must be a single student name")
        object.__setattr__(self, "cafe_invite_student", self.cafe_invite_student.strip())
        if self.cafe_invite_enabled and not self.cafe_invite_student:
            raise ConfigError("Choose cafe.invite_student before enabling invitations")
        if self.cafe_invite_enabled and not re.search(r"[A-Za-z]", self.cafe_invite_student):
            raise ConfigError("cafe.invite_student must use the English in-game name")
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
            "cafe": {"schedule_enabled", "invite_enabled", "invite_student"},
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
            values.update({f"{section}_{key}" if section in {"cafe", "lessons"} else key: value
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
