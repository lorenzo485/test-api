"""Tests for settings loading."""

import pathlib

import pytest
from pydantic import ValidationError

from test_api.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolated_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Give each test an empty environment and a directory with no `.env`."""
    monkeypatch.chdir(tmp_path)
    for key in ("ENVIRONMENT", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()


def test_defaults_apply_when_nothing_is_set() -> None:
    assert Settings().environment == "local"


def test_process_environment_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings().environment == "production"


def test_dotenv_file_is_read(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".env").write_text("ENVIRONMENT=staging\n")
    assert Settings().environment == "staging"


def test_process_environment_wins_over_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    (tmp_path / ".env").write_text("ENVIRONMENT=staging\n")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings().environment == "production"


def test_invalid_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "not-a-real-environment")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_accessor_is_cached() -> None:
    assert get_settings() is get_settings()


def test_development_environment_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert Settings().environment == "development"
