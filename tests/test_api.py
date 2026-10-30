"""Tests for the HTTP endpoints."""

import http.server
import io
import logging
import pathlib
import threading
from collections.abc import Iterator
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_api import api
from test_api.api import _warm, app
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


def test_root_serves_dashboard_to_browsers(client: TestClient) -> None:
    response = client.get("/", headers={"Accept": "text/html,*/*"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/lab/status" in response.text


@pytest.fixture
def lab(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The staging client with the metadata server stubbed out."""
    monkeypatch.setattr(api, "_region", lambda: "us-east1")
    return client


def test_lab_status_shape(lab: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K_REVISION", "test-api-00042-abc")
    body = lab.get("/lab/status").json()
    assert body == {
        "service": "test-api",
        "revision": "test-api-00042-abc",
        "environment": "staging",
        "region": "us-east1",
        "expected_region": "us-east1",
        "region_ok": True,
        "legacy": "not_configured",
        "secret": False,
        "warmup": {"done": True, "progress": 1.0},
        "user": None,
    }


def test_lab_status_reports_secret_without_leaking_it(
    lab: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_API_KEY", "s3cr3t-value")
    get_settings.cache_clear()
    response = lab.get("/lab/status")
    assert response.json()["secret"] is True
    assert "s3cr3t-value" not in response.text


def test_lab_status_strips_iap_prefix(lab: TestClient) -> None:
    response = lab.get(
        "/lab/status",
        headers={"x-goog-authenticated-user-email": "accounts.google.com:ana@gtd.cl"},
    )
    assert response.json()["user"] == "ana@gtd.cl"


def test_lab_status_reports_warmup_progress(
    lab: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WARMUP_S", "100")
    get_settings.cache_clear()
    monkeypatch.setattr(api, "_warm_started", 0.0)
    monkeypatch.setattr("test_api.api.time.monotonic", lambda: 25.0)
    _warm.clear()
    try:
        warmup = lab.get("/lab/status").json()["warmup"]
    finally:
        _warm.set()
    assert warmup == {"done": False, "progress": 0.25}


def test_lab_status_reports_unreachable_legacy(
    lab: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEGACY_URL", "http://127.0.0.1:9/")
    get_settings.cache_clear()
    assert lab.get("/lab/status").json()["legacy"] == "unreachable"


def test_lab_status_flags_region_outside_expected(
    lab: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXPECTED_REGION", "southamerica-west1")
    get_settings.cache_clear()
    body = lab.get("/lab/status").json()
    assert body["expected_region"] == "southamerica-west1"
    assert body["region_ok"] is False


@pytest.fixture
def fresh_region() -> Iterator[None]:
    """Isolate the cached metadata lookup between tests."""
    api._fetch_region.cache_clear()
    yield
    api._fetch_region.cache_clear()


@pytest.mark.usefixtures("fresh_region")
def test_region_is_unknown_when_metadata_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("no metadata server")

    monkeypatch.setattr("test_api.api.urllib.request.urlopen", refuse)
    assert api._region() == "unknown"


@pytest.mark.usefixtures("fresh_region")
def test_region_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[Exception | bytes] = [
        OSError("transient"),
        b"projects/123/regions/us-east1",
    ]

    def flaky(*_args: object, **_kwargs: object) -> io.BytesIO:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer)

    monkeypatch.setattr("test_api.api.urllib.request.urlopen", flaky)
    assert api._region() == "unknown"
    assert api._region() == "us-east1"
    assert api._region() == "us-east1"  # cached: no third lookup


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


def test_startup_fails_when_required_secret_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REQUIRE_DEMO_API_KEY", "true")
    get_settings.cache_clear()
    with pytest.raises(ValidationError), TestClient(app):
        pass
    get_settings.cache_clear()


def test_blocking_warmup_runs_before_the_app_serves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("test_api.api.time.sleep", sleeps.append)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WARMUP_S", "300")
    monkeypatch.setenv("WARMUP_MODE", "blocking")
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        assert sleeps == [300.0]
        assert test_client.get("/ready").status_code == 200
    get_settings.cache_clear()


def test_background_warmup_keeps_health_up_and_ready_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    gate = threading.Event()
    monkeypatch.setattr("test_api.api.time.sleep", lambda _s: gate.wait(5))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WARMUP_S", "300")
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200
        assert test_client.get("/ready").status_code == 503
        gate.set()
        assert _warm.wait(5)
        assert test_client.get("/ready").status_code == 200
    get_settings.cache_clear()


def test_legacy_is_off_without_url(client: TestClient) -> None:
    response = client.get("/legacy")
    assert response.status_code == 503
    assert response.json() == {"error": "legacy backend not configured"}


def test_legacy_returns_backend_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello from on-prem")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LEGACY_URL", f"http://127.0.0.1:{server.server_port}/")
        get_settings.cache_clear()
        response = client.get("/legacy")
    finally:
        server.shutdown()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "legacy": "hello from on-prem"}


def test_legacy_degrades_and_logs_when_backend_is_down(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Port 9 (discard) is closed locally, so the connection is refused fast.
    monkeypatch.setenv("LEGACY_URL", "http://127.0.0.1:9/")
    get_settings.cache_clear()
    logger = logging.getLogger("test_api.api")
    logger.addHandler(caplog.handler)
    try:
        response = client.get("/legacy")
    finally:
        logger.removeHandler(caplog.handler)
    assert response.status_code == 503
    assert response.json() == {"error": "legacy backend unavailable"}
    error = next(r for r in caplog.records if r.message == "legacy backend unavailable")
    assert error.fields["legacy_url"] == "http://127.0.0.1:9/"  # type: ignore[attr-defined]
