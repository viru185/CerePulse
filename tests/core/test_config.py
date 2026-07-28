from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from cerepulse.core.config import AppConfig, load_config, save_config
from cerepulse.core.errors import ConfigError


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump(data, handle)
    return path


def test_defaults_match_ninetofive_shift_policy() -> None:
    config = load_config(config_path=Path("does-not-exist.toml"))
    assert config.shift.work_target_hours == 8.0
    assert config.shift.break_target_hours == 1.0
    assert config.shift.shift_span_hours == 9.0


def test_user_file_overrides_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "cerepulse.toml", {"shift": {"work_target_hours": 7.5}})
    config = load_config(config_path=path)
    assert config.shift.work_target_hours == 7.5
    assert config.shift.break_target_hours == 1.0  # untouched keys keep defaults


def test_runtime_beats_user_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "cerepulse.toml", {"logging": {"level": "DEBUG"}})
    config = load_config(config_path=path, runtime={"logging": {"level": "ERROR"}})
    assert config.logging.level == "ERROR"


def test_env_override_is_applied_and_coerced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CEREPULSE__NETWORK__READ_TIMEOUT", "45")
    monkeypatch.setenv("CEREPULSE__NOTIFICATIONS__ENABLED", "false")
    config = load_config(config_path=tmp_path / "absent.toml")
    assert config.network.read_timeout == 45.0
    assert config.notifications.enabled is False


def test_user_file_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CEREPULSE__LOGGING__LEVEL", "TRACE")
    path = _write(tmp_path / "cerepulse.toml", {"logging": {"level": "WARNING"}})
    assert load_config(config_path=path).logging.level == "WARNING"


def test_invalid_toml_falls_back_to_defaults_instead_of_crashing(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("this is not = valid = toml", encoding="utf-8")
    assert load_config(config_path=path).logging.level == "INFO"


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    path = _write(tmp_path / "cerepulse.toml", {"shift": {"nonsense": 1}, "bogus": {"x": 2}})
    assert load_config(config_path=path).shift.work_target_hours == 8.0


@pytest.mark.parametrize(
    "data",
    [
        {"portal": {"base_url": "http://insecure.example.com"}},
        {"shift": {"work_target_hours": 0}},
        {"shift": {"work_target_hours": 9.0, "shift_span_hours": 8.0}},
        {"sync": {"refresh_interval_minutes": 0}},
        {"ui": {"background_mode": "always-on"}},
        {"ui": {"theme": "neon"}},
    ],
)
def test_validation_rejects_unusable_settings(tmp_path: Path, data: dict) -> None:
    path = _write(tmp_path / "cerepulse.toml", data)
    with pytest.raises(ConfigError):
        load_config(config_path=path)


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "out" / "cerepulse.toml"
    original = AppConfig.from_dict(
        {"shift": {"work_target_hours": 7.0, "workweek": [0, 1, 2, 3, 4, 5]}}
    )
    save_config(original, config_path=path)
    reloaded = load_config(config_path=path)
    assert reloaded.shift.work_target_hours == 7.0
    assert reloaded.shift.workweek == (0, 1, 2, 3, 4, 5)


def test_saved_config_never_contains_a_password(tmp_path: Path) -> None:
    path = tmp_path / "cerepulse.toml"
    save_config(AppConfig.from_dict({"portal": {"username": "CIPL00364"}}), config_path=path)
    text = path.read_text(encoding="utf-8")
    assert "CIPL00364" in text
    assert "password" not in text.lower()
