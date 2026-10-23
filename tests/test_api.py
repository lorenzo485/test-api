"""Tests for the HTTP endpoints."""

import logging
import pathlib
from collections.abc import Iterator
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from test_api.api import app
from test_api.config import get_settings


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> Iterator[TestClient]:
    """Serve the app with ENVIRONMENT=staging and no stray `.env` file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_environment(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "environment": "staging"}


def test_root_greets_with_environment(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["environment"] == "staging"
    assert body["service"] == "test-api"


def test_version_reports_package_version(client: TestClient) -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": version("test-api")}


def test_checkout_succeeds_without_fault_injection(client: TestClient) -> None:
    response = client.get("/checkout")
    assert response.status_code == 200
    assert response.json() == {"message": "checkout exitoso"}


def test_checkout_fails_when_error_rate_is_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ERROR_RATE", "1")
    get_settings.cache_clear()
    assert client.get("/checkout").status_code == 500


def test_checkout_sleeps_slow_ms_when_slow_rate_is_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("test_api.api.time.sleep", sleeps.append)
    monkeypatch.setenv("SLOW_RATE", "1")
    monkeypatch.setenv("SLOW_MS", "1500")
    get_settings.cache_clear()
    assert client.get("/checkout").status_code == 200
    assert sleeps == [1.5]


def test_access_log_records_checkout_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_api.api")
    logger.addHandler(caplog.handler)
    monkeypatch.setenv("ERROR_RATE", "1")
    get_settings.cache_clear()
    try:
        client.get("/checkout")
    finally:
        logger.removeHandler(caplog.handler)
    (record,) = caplog.records
    assert record.levelname == "ERROR"
    fields = record.fields  # type: ignore[attr-defined]
    assert fields["path"] == "/checkout"
    assert fields["status"] == 500
    assert fields["latency_ms"] >= 0


def test_access_log_records_checkout_success(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("test_api.api")
    logger.addHandler(caplog.handler)
    try:
        client.get("/checkout")
    finally:
        logger.removeHandler(caplog.handler)
    (record,) = caplog.records
    assert record.levelname == "INFO"
    fields = record.fields  # type: ignore[attr-defined]
    assert fields["path"] == "/checkout"
    assert fields["status"] == 200
    assert fields["latency_ms"] >= 0
