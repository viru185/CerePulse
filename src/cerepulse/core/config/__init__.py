"""Application configuration."""

from __future__ import annotations

from cerepulse.core.config.loader import ENV_PREFIX, load_config, save_config
from cerepulse.core.config.models import (
    AppConfig,
    LoggingConfig,
    NetworkConfig,
    NotificationConfig,
    PortalConfig,
    ShiftConfig,
    SyncConfig,
    UiConfig,
    UpdateConfig,
)

__all__ = [
    "ENV_PREFIX",
    "AppConfig",
    "LoggingConfig",
    "NetworkConfig",
    "NotificationConfig",
    "PortalConfig",
    "ShiftConfig",
    "SyncConfig",
    "UiConfig",
    "UpdateConfig",
    "load_config",
    "save_config",
]
