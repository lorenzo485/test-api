"""HTTP surface served on Cloud Run.

`/health` only proves the process answers; `/ready` proves configuration
loaded and the warmup finished. Settings validate at startup: Cloud Run has
no crash loop, so a bad configuration fails the new revision and traffic
stays on the previous one.
"""

import functools
import http.client
import logging
import os
import random
import threading
import time
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from test_api.config import get_settings
from test_api.dashboard import DASHBOARD_HTML
from test_api.logs import get_logger

logger = get_logger(__name__)
_warm = threading.Event()
_warm_started = 0.0  # monotonic start of the last warmup, for /lab/status
_HTTP_ERRORS = (OSError, ValueError, http.client.HTTPException)
_METADATA_REGION_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/region"
)


def _warm_up(seconds: float) -> None:
    """Stand in for synchronous connections to slow legacy systems."""
    global _warm_started
    _warm_started = time.monotonic()
    time.sleep(seconds)
    _warm.set()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate settings, then warm up before or after uvicorn binds $PORT.

    Uvicorn finishes lifespan startup before it binds the socket, so an
    invalid setting exits before listening and a blocking warmup keeps the
    port closed until the startup probe gives up.
    """
    _warm.clear()
    settings = get_settings()
    if settings.warmup_mode == "blocking" or not settings.warmup_s:
        _warm_up(settings.warmup_s)
    else:
        threading.Thread(
            target=_warm_up, args=(settings.warmup_s,), daemon=True
        ).start()
    yield


app = FastAPI(title="test-api", lifespan=lifespan)


@app.middleware("http")
async def access_log(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Emit one structured log line per request, the source of the SLIs.

    Args:
        request: The incoming request.
        call_next: The rest of the middleware chain.

    Returns:
        The downstream response, unchanged.
    """
    start = time.perf_counter()
    # 499 = client closed the request: a cancellation (e.g. a load generator
    # timeout during a latency incident) must not burn the availability budget.
    status = 499
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        status = 500  # an unhandled exception still counts against availability
        raise
    finally:
        logger.log(
            logging.ERROR if status >= 500 else logging.INFO,
            "request completed",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                }
            },
        )


@app.get("/", response_model=None)
def root(request: Request) -> dict[str, Any] | HTMLResponse:
    """Return a greeting that shows which environment answered.

    Browsers get the live lab dashboard; curl and other clients keep JSON.

    Args:
        request: The incoming request, read for its Accept header.

    Returns:
        The dashboard page, or service name, greeting, and environment.
    """
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(DASHBOARD_HTML)
    return {
        "service": "test-api",
        "message": "Hello from Cloud Run",
        "environment": get_settings().environment,
    }


@app.get("/health")
def health() -> dict[str, str]:
    """Report liveness without touching configuration or dependencies.

    Returns:
        A constant OK status.
    """
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    """Report readiness once settings validate and the warmup finished.

    Returns:
        Ready status plus the configured environment.

    Raises:
        HTTPException: 503 while the background warmup is still running.
    """
    settings = get_settings()
    if not _warm.is_set():
        raise HTTPException(status_code=503, detail="warming up")
    return {"status": "ready", "environment": settings.environment}


@app.get("/legacy", response_model=None)
def legacy() -> dict[str, str] | JSONResponse:
    """Call the private on-prem backend and degrade gracefully when it fails.

    Returns:
        The backend's first bytes, or a friendly 503 when it is unreachable.
    """
    url = get_settings().legacy_url
    if not url:
        return JSONResponse(
            status_code=503, content={"error": "legacy backend not configured"}
        )
    try:
        # A short timeout: a hung on-prem system must not hold the request
        # until Cloud Run's 30 s timeout.
        body = _call_legacy(url, timeout=2)
    except _HTTP_ERRORS as error:
        logger.error(
            "legacy backend unavailable",
            extra={"fields": {"legacy_url": url, "error": repr(error)}},
        )
        return JSONResponse(
            status_code=503, content={"error": "legacy backend unavailable"}
        )
    return {"status": "ok", "legacy": body}


def _call_legacy(url: str, timeout: float) -> str:
    """Fetch the first bytes of the on-prem backend.

    Args:
        url: The backend URL.
        timeout: Seconds to wait before giving up.

    Returns:
        Up to 200 bytes of the response body, decoded.

    Raises:
        OSError: The backend is unreachable or timed out.
        ValueError: The URL is malformed.
        http.client.HTTPException: The backend spoke broken HTTP.
    """
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read(200).decode("utf-8", errors="replace")


@functools.cache
def _fetch_region() -> str:
    """Read the Cloud Run region from the metadata server.

    Cached, and raising on failure, so only a successful lookup sticks.

    Raises:
        OSError, ValueError, http.client.HTTPException: Metadata unreachable.
    """
    request = urllib.request.Request(
        _METADATA_REGION_URL, headers={"Metadata-Flavor": "Google"}
    )
    with urllib.request.urlopen(request, timeout=1) as response:
        # The answer is projects/<number>/regions/<region>.
        region = response.read().decode().rsplit("/", 1)[-1]
    if not region:
        raise ValueError("empty region from metadata server")
    return region


def _region() -> str:
    """Return the Cloud Run region, retrying the lookup until it succeeds.

    Returns:
        The region name, or "unknown" outside Google Cloud.
    """
    try:
        return _fetch_region()
    except _HTTP_ERRORS:
        return "unknown"


@app.get("/lab/status")
def lab_status(request: Request) -> dict[str, Any]:
    """Report each production-lab check for the live dashboard.

    Polled every 2 s, so it never logs failures and uses short timeouts.

    Args:
        request: The incoming request, read for the IAP identity header.

    Returns:
        Revision, region, on-prem reachability, secret presence (never its
        value), warmup progress, and the IAP user.
    """
    settings = get_settings()
    region = _region()
    if not settings.legacy_url:
        legacy = "not_configured"
    else:
        try:
            _call_legacy(settings.legacy_url, timeout=1)
            legacy = "reachable"
        except _HTTP_ERRORS:
            legacy = "unreachable"
    done = _warm.is_set() or not settings.warmup_s
    progress = (
        1.0
        if done
        else min((time.monotonic() - _warm_started) / settings.warmup_s, 1.0)
    )
    # Display only: the header is trustworthy only behind IAP.
    user = request.headers.get("x-goog-authenticated-user-email")
    return {
        "service": os.environ.get("K_SERVICE", "test-api"),
        "revision": os.environ.get("K_REVISION", "local"),
        "environment": settings.environment,
        "region": region,
        "expected_region": settings.expected_region,
        "region_ok": region == settings.expected_region,
        "legacy": legacy,
        "secret": bool(settings.demo_api_key.get_secret_value()),
        "warmup": {"done": done, "progress": round(progress, 3)},
        "user": user.removeprefix("accounts.google.com:") if user else None,
    }


@app.get("/checkout")
def checkout() -> dict[str, str]:
    """Confirm a checkout, degraded on purpose by the fault-injection knobs.

    `ERROR_RATE` fails a share of requests with 500 (availability SLI) and
    `SLOW_RATE` delays a share by `SLOW_MS` (latency SLI).

    Returns:
        A success message.

    Raises:
        HTTPException: 500 for the injected share of failed requests.
    """
    settings = get_settings()
    if random.random() < settings.error_rate:
        raise HTTPException(status_code=500, detail="checkout failed")
    if random.random() < settings.slow_rate:
        time.sleep(settings.slow_ms / 1000)
    return {"message": "checkout exitoso"}


@app.get("/version")
def get_version() -> dict[str, str]:
    """Report the installed package version.

    Returns:
        The version from the package metadata.
    """
    return {"version": version("test-api")}


@app.get("/test")
def test_function() -> dict[str, str]:
    """Test funcion, this does nothing.

    Returns:
        anything really, does not matter.
    """
    return {"message": "hola papito lindo, esta es la funcion de test"}
