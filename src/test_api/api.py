"""HTTP surface served on Cloud Run.

Cloud Run probes the container over HTTP, so liveness and readiness are split:
`/health` only proves the process answers, `/ready` proves configuration
loaded. A bad configuration then fails readiness instead of restarting forever.
"""

from typing import Any

from fastapi import FastAPI

from test_api.config import get_settings

app = FastAPI(title="test-api")


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
