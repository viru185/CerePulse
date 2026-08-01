"""Typed configuration models (Chapter 13 section 4).

Plain frozen dataclasses rather than a validation framework — the surface is small and
every field has a safe default, so a malformed user config degrades to defaults instead of
refusing to start.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Self, get_args, get_origin

from cerepulse.core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class PortalConfig:
    """Where the HR portal lives and which tenant to sign into."""

    base_url: str = "https://cerebulb.spinehr.in"
    company_code: str = "CEREBU"
    connect_as: str = "User"
    username: str = ""
    remember_me: bool = False


@dataclass(frozen=True, slots=True)
class ShiftConfig:
    """Shift policy. Defaults match ninetofive: 8h work + 1h break = a 9h span."""

    work_target_hours: float = 8.0
    break_target_hours: float = 1.0
    shift_span_hours: float = 9.0
    workweek: tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday=0 .. Sunday=6


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """Split timeouts and retry policy (Chapter 03 sections 8-9)."""

    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    write_timeout: float = 30.0
    pool_timeout: float = 5.0
    max_retries: int = 3
    backoff_factor: float = 0.5
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    )


@dataclass(frozen=True, slots=True)
class SyncConfig:
    refresh_interval_minutes: int = 15
    cache_ttl_minutes: int = 10
    history_months: int = 12


@dataclass(frozen=True, slots=True)
class UiConfig:
    theme: str = "dark"  # dark | light | system
    background_mode: str = "tray"  # tray | foreground
    start_with_windows: bool = False
    tone: str = "playful"  # playful | plain


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    """Master switch plus one toggle per alert, as agreed."""

    enabled: bool = True
    quiet_hours_start: str = "21:00"
    quiet_hours_end: str = "08:00"
    work_target_reached: bool = True
    break_exceeded: bool = True
    short_hours_warning: bool = True
    swipe_request_needed: bool = True
    leave_expiring: bool = True


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str = "INFO"


@dataclass(frozen=True, slots=True)
class UpdateConfig:
    check_on_startup: bool = True
    #: stable | beta. A typo resolves to stable, so a mistyped value cannot opt someone
    #: into prereleases.
    channel: str = "stable"
    #: Fetch the installer in the background as soon as one is found. Installing still
    #: waits for an explicit yes.
    download_automatically: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Root configuration object."""

    portal: PortalConfig = field(default_factory=PortalConfig)
    shift: ShiftConfig = field(default_factory=ShiftConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    updates: UpdateConfig = field(default_factory=UpdateConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build from a merged mapping, ignoring unknown keys and coercing scalars."""
        kwargs: dict[str, Any] = {}
        for section in fields(cls):
            section_data = data.get(section.name)
            if not isinstance(section_data, dict):
                continue
            kwargs[section.name] = _build_section(section.name, section_data)
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            section.name: _section_to_dict(getattr(self, section.name)) for section in fields(self)
        }


def _build_section(section_name: str, data: dict[str, Any]) -> Any:
    # dataclass field types arrive as strings under `from __future__ import annotations`,
    # so sections are resolved by name rather than from the annotation.
    section_cls = _SECTION_TYPES[section_name]
    kwargs: dict[str, Any] = {}
    for f in fields(section_cls):
        if f.name not in data:
            continue
        kwargs[f.name] = _coerce(data[f.name], f.type, f"{section_name}.{f.name}")
    return section_cls(**kwargs)


def _section_to_dict(section: Any) -> dict[str, Any]:
    if not is_dataclass(section):
        return {}
    out: dict[str, Any] = {}
    for f in fields(section):
        value = getattr(section, f.name)
        out[f.name] = list(value) if isinstance(value, tuple) else value
    return out


def _coerce(value: Any, declared: Any, path: str) -> Any:
    """Coerce a scalar into the declared field type. Env vars arrive as strings."""
    type_name = declared if isinstance(declared, str) else getattr(declared, "__name__", "")

    try:
        if type_name.startswith("tuple"):
            if isinstance(value, str):
                value = [part.strip() for part in value.split(",") if part.strip()]
            return tuple(int(item) for item in value)
        if type_name == "bool":
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if type_name == "int":
            return int(value)
        if type_name == "float":
            return float(value)
        if type_name == "str":
            return str(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid value for {path!r}: {value!r}") from exc

    if get_origin(declared) is not None and get_args(declared):
        return value
    return value


_SECTION_TYPES: dict[str, Any] = {
    "portal": PortalConfig,
    "shift": ShiftConfig,
    "network": NetworkConfig,
    "sync": SyncConfig,
    "ui": UiConfig,
    "notifications": NotificationConfig,
    "logging": LoggingConfig,
    "updates": UpdateConfig,
}
