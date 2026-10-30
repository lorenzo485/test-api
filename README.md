# test-api

Reference repository for CI/CD best practices. A minimal FastAPI service on
Cloud Run (`/`, `/health`, `/ready`) whose real subject is the pipeline around
it: quality and security gates, keyless GCP auth, and a single trunk-based
deploy.

A push to `main` runs `.github/workflows/pipeline.yml`: lint, typecheck,
tests, SAST (Semgrep), and SCA (Trivy) in parallel. When all five pass, one
job builds the SHA-tagged image and deploys it to Cloud Run in the only
environment, `production`.

Authentication uses GitHub OIDC with Workload Identity Federation, so no
service account keys exist. GCP and GitHub setup scripts live in `infra/`.

## Setup

```bash
cp .env.example .env
uv sync
```

`uv sync` creates `.venv`, installs runtime and dev dependencies, and installs
`src/test_api` in editable mode so imports resolve without path manipulation.

## Commands

```bash
uv run test-api           # run the entry point
uv run pytest          # run tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run pyright         # type check
```

## Lab knobs

Off by default; the session III lab (`scripts/lab-produccion.md`) flips them.
The SLO lab knobs (`SLOW_RATE`, `SLOW_MS`, `ERROR_RATE`) are in
`scripts/chaos-commands.md`.

| Variable | Effect |
|---|---|
| `REQUIRE_DEMO_API_KEY` | `true` makes an empty `DEMO_API_KEY` abort startup |
| `LEGACY_URL` | Private backend called by `GET /legacy` (2 s timeout, 503 on failure) |
| `WARMUP_S` | Seconds of simulated legacy connections at startup |
| `WARMUP_MODE` | `blocking` before the port binds, or `background` behind `/ready` |

## Dependencies

```bash
uv add <package>              # runtime dependency
uv add --dev <package>        # dev-only dependency
uv lock --upgrade-package <package>
uv sync                       # reconcile .venv with uv.lock
```

Commit `uv.lock`. It is what makes installs reproducible across machines and
CI, which is why `pyproject.toml` declares `>=` floors instead of exact pins.
