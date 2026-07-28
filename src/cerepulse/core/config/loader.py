"""Configuration loading (Chapter 13 section 3).

Resolution order, later layers winning:

    defaults  ->  environment overrides  ->  user config file  ->  runtime values

Environment overrides use ``CEREPULSE__<SECTION>__<KEY>`` (double underscore separators),
e.g. ``CEREPULSE__NETWORK__READ_TIMEOUT=45``.
"""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import tomli_w
from loguru import logger

from cerepulse.core import paths
from cerepulse.core.config.models import AppConfig
from cerepulse.core.errors import ConfigError

ENV_PREFIX = "CEREPULSE__"


def load_config(
    *,
    config_path: Path | None = None,
    runtime: dict[str, Any] | None = None,
) -> AppConfig:
    """Build the effective configuration from all four layers."""
    merged = AppConfig().to_dict()  # layer 1: defaults
    _deep_merge(merged, _env_overrides())  # layer 2: environment
    _deep_merge(merged, _read_file(config_path or paths.config_file()))  # layer 3: user file
    _deep_merge(merged, runtime or {})  # layer 4: runtime

    config = AppConfig.from_dict(merged)
    _validate(config)
    return config


def save_config(config: AppConfig, *, config_path: Path | None = None) -> Path:
    """Write the user configuration file. Secrets never pass through here."""
    target = config_path or paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        tomli_w.dump(config.to_dict(), handle)
    logger.info("Saved configuration to {}", target)
    return target


def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # A broken user config must not prevent startup — fall back to defaults loudly.
        logger.error("Ignoring invalid config at {}: {}", path, exc)
        return {}


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        remainder = key[len(ENV_PREFIX) :]
        if "__" not in remainder:
            continue
        section, _, option = remainder.partition("__")
        overrides.setdefault(section.lower(), {})[option.lower()] = value
    return overrides


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def _validate(config: AppConfig) -> None:
    """Reject settings that would make the app misbehave rather than merely look odd."""
    if not config.portal.base_url.startswith("https://"):
        raise ConfigError("portal.base_url must be an https:// URL")
    if config.shift.work_target_hours <= 0:
        raise ConfigError("shift.work_target_hours must be greater than zero")
    if config.shift.shift_span_hours < config.shift.work_target_hours:
        raise ConfigError("shift.shift_span_hours cannot be less than shift.work_target_hours")
    if config.sync.refresh_interval_minutes < 1:
        raise ConfigError("sync.refresh_interval_minutes must be at least 1")
    if config.ui.background_mode not in {"tray", "foreground"}:
        raise ConfigError("ui.background_mode must be 'tray' or 'foreground'")
    if config.ui.theme not in {"dark", "light", "system"}:
        raise ConfigError("ui.theme must be 'dark', 'light' or 'system'")
