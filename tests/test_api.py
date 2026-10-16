"""Tests for the HTTP endpoints."""

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
