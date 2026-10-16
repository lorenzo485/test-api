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
| Service accounts | `sa-runtime-development`, `sa-runtime-staging`, `sa-runtime-production` | Identity each Cloud Run service runs as |
| IAM binding | `roles/iam.workloadIdentityUser` on each deployer SA | Only its own `attribute.environment/<env>` can impersonate it |
| Artifact Registry | Docker repository `test-api` in `us-east1` | Images tagged with the commit SHA, shared by every environment |
| IAM binding | `roles/run.admin` (project) on each deployer SA | Lets the pipeline run `gcloud run services replace` |
| IAM binding | `roles/artifactregistry.writer` on the `test-api` repository only | Push images, nothing else in Artifact Registry |
| IAM binding | `roles/iam.serviceAccountUser` on `sa-runtime-<env>` only | Each deployer can deploy as its own runtime SA, never another environment's |
| Secret | `demo-api-key` (random value) | Demo business secret; `secretAccessor` granted to the three SAs on the secret only |

The provider maps `google.subject`, `attribute.repository`,
`attribute.repository_owner`, `attribute.ref`, and `attribute.environment`,
and its attribute condition rejects any token whose `repository_owner` is not
`lorenzo485` or whose `repository` is not `lorenzo485/test-api`.

## Prerequisites

- `gcloud` authenticated with rights to manage IAM, services, and secrets in
  the project, plus `openssl`.
- GitHub Environments `development`, `staging`, and `production`, created by
  `setup-github.sh` below. `production` must have at least one required
  reviewer: that approval is the gate in front of the production service
  account.

## Run

```bash
bash infra/gcp/setup-wif.sh
```

The script is re-runnable: it checks each resource with `describe` before
creating it. Defaults can be overridden with environment variables
(`PROJECT_ID`, `REGION`, `GITHUB_OWNER`, `GITHUB_REPO`, `POOL_ID`,
`PROVIDER_ID`, `SECRET_ID`, `AR_REPO`). IAM changes can take 2-7 minutes to propagate.

## GitHub variables

`setup-github.sh` creates the three Environments (production with the `gh`
user, or `PROD_REVIEWER`, as required reviewer) and sets these variables. Run
it after `setup-wif.sh`; it needs `gh` authenticated with the `repo` scope.

```bash
bash infra/gcp/setup-github.sh
```

They are `vars`, not secrets: they are identifiers, not credentials.

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

## Deployer vs runtime identity

The deployer SA is what GitHub Actions impersonates; the runtime SA is what
the Cloud Run service runs as (`serviceAccountName` in
`deploy/cloudrun/service-<env>.yaml`). They are kept apart so the running app
never holds deploy permissions. `roles/iam.serviceAccountUser` is granted on
the runtime SA itself, not the project, so `sa-deployer-development` cannot
deploy a service that runs as `sa-runtime-production`.

The services are private: no `allUsers` invoker binding is created. To call
one during the demo, use an identity token:

```bash
URL="$(gcloud run services describe test-api-staging --region=us-east1 \
  --format='value(status.url)')"
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  "${URL}/ready"
```
