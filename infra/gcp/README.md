# GCP: keyless GitHub Actions auth

`setup-wif.sh` prepares project `lorenz-1234` (region `us-east1`) so GitHub
Actions in `lorenzo485/test-api` can authenticate to Google Cloud without any
JSON key, using Workload Identity Federation (WIF).

## What it creates

| Resource | Name | Purpose |
| --- | --- | --- |
| APIs | iam, iamcredentials, sts, secretmanager, run, artifactregistry | Required by WIF, secrets, and the later Cloud Run deploy |
| Workload identity pool | `github` | Trust container for external identities |
| OIDC provider | `github-actions` | Trusts `https://token.actions.githubusercontent.com`, only for `lorenzo485/test-api` |
| Service accounts | `sa-deployer-development`, `sa-deployer-staging`, `sa-deployer-production` | One deployer per GitHub Environment |
| IAM binding | `roles/iam.workloadIdentityUser` on each SA | Only its own `attribute.environment/<env>` can impersonate it |
| Secret | `demo-api-key` (random value) | Demo business secret; `secretAccessor` granted to the three SAs on the secret only |

The provider maps `google.subject`, `attribute.repository`,
`attribute.repository_owner`, `attribute.ref`, and `attribute.environment`,
and its attribute condition rejects any token whose `repository_owner` is not
`lorenzo485` or whose `repository` is not `lorenzo485/test-api`.

## Prerequisites

- `gcloud` authenticated with rights to manage IAM, services, and secrets in
  the project, plus `openssl`.
- GitHub Environments `development`, `staging`, and `production` exist in the
  repository. `production` must have at least one required reviewer: that
  approval is the gate in front of the production service account.

## Run

```bash
bash infra/gcp/setup-wif.sh
```

The script is re-runnable: it checks each resource with `describe` before
creating it. Defaults can be overridden with environment variables
(`PROJECT_ID`, `REGION`, `GITHUB_OWNER`, `GITHUB_REPO`, `POOL_ID`,
`PROVIDER_ID`, `SECRET_ID`). IAM changes can take 2-7 minutes to propagate.

## GitHub variables

The script prints these values and the `gh variable set` commands; it does not
run them. They are `vars`, not secrets: they are identifiers, not credentials.

| Variable | Scope | Value |
| --- | --- | --- |
| `WIF_PROVIDER` | repository | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-actions` |
| `DEPLOY_SA` | environment `development` | `sa-deployer-development@lorenz-1234.iam.gserviceaccount.com` |
| `DEPLOY_SA` | environment `staging` | `sa-deployer-staging@lorenz-1234.iam.gserviceaccount.com` |
| `DEPLOY_SA` | environment `production` | `sa-deployer-production@lorenz-1234.iam.gserviceaccount.com` |

Verify with the `wif-smoke` workflow (`.github/workflows/wif-smoke.yml`).

## Why bind on `attribute.environment`, not `ref`

The deck binds the service account to `assertion.ref == "refs/heads/main"`.
That contradicts its own branch model: staging deploys run from the `staging`
branch and production deploys run from `v*` tags, so neither would ever get a
token.

Binding each SA to `principalSet://.../attribute.environment/<env>` follows
the deployment target instead of the git ref. GitHub only puts
`environment: production` in the OIDC token for a job that runs in the
`production` Environment, which in turn waits for its required reviewers. The
production SA is therefore reachable only after a human approval, whatever
branch or tag triggered the run.
