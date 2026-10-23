"""HTTP surface served on Cloud Run.

Cloud Run probes the container over HTTP, so liveness and readiness are split:
`/health` only proves the process answers, `/ready` proves configuration
loaded. A bad configuration then fails readiness instead of restarting forever.
"""

import logging
import random
import time
from collections.abc import Awaitable, Callable
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

from test_api.config import get_settings
from test_api.logs import get_logger

app = FastAPI(title="test-api")
logger = get_logger(__name__)


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
    status = 500  # an unhandled exception still counts against availability
    try:
        response = await call_next(request)
        status = response.status_code
        return response
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


@app.get("/")
def root() -> dict[str, Any]:
    """Return a greeting that shows which environment answered.

    Returns:
        Service name, greeting, and the configured environment.
    """
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
    """Report readiness once settings validate.

    Returns:
        Ready status plus the configured environment.
    """
    return {"status": "ready", "environment": get_settings().environment}


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
