# test-api

Reference repository for CI/CD best practices. A minimal FastAPI service on
Cloud Run (`/`, `/health`, `/ready`) whose real subject is the pipeline around
it: quality and security gates, keyless GCP auth, and a branch-driven
promotion path.

| Trigger                    | Result                                          |
| -------------------------- | ----------------------------------------------- |
| Pull request               | `ci` + `security` gates; deploy to development  |
| Merge into `staging`       | Build image and deploy to staging               |
| Tag `v*` (from `staging`)  | Promote the staging image to production         |

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

## Dependencies

```bash
uv add <package>              # runtime dependency
uv add --dev <package>        # dev-only dependency
uv lock --upgrade-package <package>
uv sync                       # reconcile .venv with uv.lock
```

Commit `uv.lock`. It is what makes installs reproducible across machines and
CI, which is why `pyproject.toml` declares `>=` floors instead of exact pins.
