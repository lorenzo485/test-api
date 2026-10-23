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
| Service account | `sa-deployer-production` | Deployer for the `production` GitHub Environment |
| Service account | `sa-runtime-production` | Identity the Cloud Run service runs as |
| IAM binding | `roles/iam.workloadIdentityUser` on the deployer SA | Only `attribute.environment/production` can impersonate it |
| Artifact Registry | Docker repository `test-api` in `us-east1` | Images tagged with the commit SHA |
| IAM binding | `roles/run.admin` (project) on the deployer SA | Lets the pipeline run `gcloud run services replace` |
| IAM binding | `roles/artifactregistry.writer` on the `test-api` repository only | Push images, nothing else in Artifact Registry |
| IAM binding | `roles/iam.serviceAccountUser` on `sa-runtime-production` only | The deployer can deploy as that runtime SA, no other |
| Secret | `demo-api-key` (random value) | Demo business secret; `secretAccessor` granted to the deployer SA on the secret only |

The provider maps `google.subject`, `attribute.repository`,
`attribute.repository_owner`, `attribute.ref`, and `attribute.environment`,
and its attribute condition rejects any token whose `repository_owner` is not
`lorenzo485` or whose `repository` is not `lorenzo485/test-api`.

## Prerequisites

- `gcloud` authenticated with rights to manage IAM, services, and secrets in
  the project, plus `openssl`.
- The GitHub Environment `production`, created by `setup-github.sh` below
  with no required reviewers.

## Deploying to production

Push to `main`. `.github/workflows/pipeline.yml` runs every check in parallel
and, only if all pass, builds the image and deploys it. No approval step.

## Run

```bash
bash infra/gcp/setup-wif.sh
```

The script is re-runnable: it checks each resource with `describe` before
creating it. Defaults can be overridden with environment variables
(`PROJECT_ID`, `REGION`, `GITHUB_OWNER`, `GITHUB_REPO`, `POOL_ID`,
`PROVIDER_ID`, `SECRET_ID`, `AR_REPO`). IAM changes can take 2-7 minutes to propagate.

## GitHub variables

`setup-github.sh` creates the `production` Environment (no required
reviewers) and sets these variables. Run it
after `setup-wif.sh`. It needs a `gh` login for `GH_USER` (default
`lorenzo485`, a repo admin) with the `repo` scope; that account does not have
to be the active one, the script uses its token for its own calls only.

```bash
bash infra/gcp/setup-github.sh
```

They are `vars`, not secrets: they are identifiers, not credentials.

| Variable | Scope | Value |
| --- | --- | --- |
| `WIF_PROVIDER` | repository | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-actions` |
| `DEPLOY_SA` | environment `production` | `sa-deployer-production@lorenz-1234.iam.gserviceaccount.com` |

Verify by running the `pipeline` workflow (`workflow_dispatch`).

## Why bind on `attribute.environment`, not `ref`

Binding the SA to `principalSet://.../attribute.environment/production`
follows the deployment target instead of the git ref. GitHub only puts
`environment: production` in the OIDC token for a job that declares that
Environment, so another workflow or job in the repo cannot impersonate the
deployer just by running on `main`.

## Deployer vs runtime identity

The deployer SA is what GitHub Actions impersonates; the runtime SA is what
the Cloud Run service runs as (`serviceAccountName` in
`deploy/cloudrun/service-production.yaml`). They are kept apart so the running
app never holds deploy permissions. `roles/iam.serviceAccountUser` is granted
on the runtime SA itself, not the project, so the deployer cannot deploy a
service that runs as any other identity.

The service is private: no `allUsers` invoker binding is created. To call it
during the demo, use an identity token:

```bash
URL="$(gcloud run services describe test-api-production --region=us-east1 \
  --format='value(status.url)')"
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  "${URL}/ready"
```
