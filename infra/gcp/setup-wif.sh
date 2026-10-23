#!/bin/bash
# -*- coding: utf-8 -*-
# Language: Bash / Shell
# One-time GCP setup for keyless GitHub Actions auth (Workload Identity
# Federation), one deployer and one runtime service account per GitHub
# Environment, the Artifact Registry repository for images, and a demo secret
# in Secret Manager. Re-runnable: every create is guarded by a
# describe. See infra/gcp/README.md.

set -euo pipefail
IFS=$'\n\t'

# Configuration (override via environment variables).
PROJECT_ID="${PROJECT_ID:-lorenz-1234}"
REGION="${REGION:-us-east1}"
GITHUB_OWNER="${GITHUB_OWNER:-lorenzo485}"
GITHUB_REPO="${GITHUB_REPO:-test-api}"
POOL_ID="${POOL_ID:-github}"
PROVIDER_ID="${PROVIDER_ID:-github-actions}"
SECRET_ID="${SECRET_ID:-demo-api-key}"
AR_REPO="${AR_REPO:-test-api}"
ENVIRONMENTS=(production)

readonly PROJECT_ID REGION GITHUB_OWNER GITHUB_REPO POOL_ID PROVIDER_ID
readonly SECRET_ID AR_REPO ENVIRONMENTS

log_info() { echo "[INFO]  $(date -Iseconds) $*"; }
log_error() { echo "[ERROR] $(date -Iseconds) $*" >&2; }

# Prints the deployer service account id for an environment.
# Args: $1 environment name. Output: account id (no domain).
sa_id_for() {
  echo "sa-deployer-$1"
}

# Prints the deployer service account email for an environment.
# Args: $1 environment name.
sa_email_for() {
  echo "$(sa_id_for "$1")@${PROJECT_ID}.iam.gserviceaccount.com"
}

# Prints the Cloud Run runtime service account id for an environment.
# Args: $1 environment name. Output: account id (no domain).
runtime_sa_id_for() {
  echo "sa-runtime-$1"
}

# Prints the runtime service account email for an environment.
# Args: $1 environment name.
runtime_sa_email_for() {
  echo "$(runtime_sa_id_for "$1")@${PROJECT_ID}.iam.gserviceaccount.com"
}

# Fails early when a required CLI is missing.
require_commands() {
  local cmd
  for cmd in gcloud openssl; do
    command -v "${cmd}" >/dev/null 2>&1 || {
      log_error "required command not found: ${cmd}"
      exit 1
    }
  done
}

# Service account ids must be 6-30 chars; catch it before gcloud does.
validate_sa_ids() {
  local env id
  for env in "${ENVIRONMENTS[@]}"; do
    for id in "$(sa_id_for "${env}")" "$(runtime_sa_id_for "${env}")"; do
      if ((${#id} > 30)); then
        log_error "service account id too long (${#id} > 30): ${id}"
        exit 2
      fi
    done
  done
}

enable_apis() {
  log_info "enabling APIs in ${PROJECT_ID}"
  gcloud services enable \
    iam.googleapis.com \
    iamcredentials.googleapis.com \
    sts.googleapis.com \
    secretmanager.googleapis.com \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    --project="${PROJECT_ID}"
}

create_pool() {
  if gcloud iam workload-identity-pools describe "${POOL_ID}" \
    --project="${PROJECT_ID}" --location=global >/dev/null 2>&1; then
    log_info "pool ${POOL_ID} already exists"
    return
  fi
  log_info "creating workload identity pool ${POOL_ID}"
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --project="${PROJECT_ID}" --location=global \
    --display-name="GitHub Actions"
}

# The condition is the trust boundary: only tokens minted for this exact
# repository (and owner) are accepted, so no other GitHub repo can use the
# pool even if it guesses the provider name. attribute.environment carries
# the GitHub Environment name used by the per-environment bindings below.
create_provider() {
  local mapping condition
  mapping="google.subject=assertion.sub"
  mapping+=",attribute.repository=assertion.repository"
  mapping+=",attribute.repository_owner=assertion.repository_owner"
  mapping+=",attribute.ref=assertion.ref"
  mapping+=",attribute.environment=assertion.environment"
  condition="assertion.repository_owner == '${GITHUB_OWNER}'"
  condition+=" && assertion.repository == '${GITHUB_OWNER}/${GITHUB_REPO}'"

  if gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
    --project="${PROJECT_ID}" --location=global \
    --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
    log_info "provider ${PROVIDER_ID} exists; updating mapping/condition"
    gcloud iam workload-identity-pools providers update-oidc \
      "${PROVIDER_ID}" \
      --project="${PROJECT_ID}" --location=global \
      --workload-identity-pool="${POOL_ID}" \
      --attribute-mapping="${mapping}" \
      --attribute-condition="${condition}"
    return
  fi
  log_info "creating OIDC provider ${PROVIDER_ID}"
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --project="${PROJECT_ID}" --location=global \
    --workload-identity-pool="${POOL_ID}" \
    --display-name="GitHub Actions OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="${mapping}" \
    --attribute-condition="${condition}"
}

# Creates a service account unless it already exists.
# Args: $1 account id, $2 display name.
ensure_service_account() {
  local id="$1" display_name="$2"
  if gcloud iam service-accounts describe \
    "${id}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    log_info "service account ${id} already exists"
    return
  fi
  log_info "creating service account ${id}"
  gcloud iam service-accounts create "${id}" \
    --project="${PROJECT_ID}" \
    --display-name="${display_name}"
}

# Deployer SAs are what GitHub impersonates; runtime SAs are what the Cloud
# Run service runs as. Keeping them apart means the running app never holds
# deploy permissions.
create_service_accounts() {
  local env
  for env in "${ENVIRONMENTS[@]}"; do
    ensure_service_account "$(sa_id_for "${env}")" "GitHub deployer (${env})"
    ensure_service_account "$(runtime_sa_id_for "${env}")" \
      "Cloud Run runtime (${env})"
  done
}

# One regional Docker repository holds the sha-tagged images the pipeline
# builds and deploys.
create_artifact_registry() {
  if gcloud artifacts repositories describe "${AR_REPO}" \
    --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
    log_info "artifact registry repository ${AR_REPO} already exists"
    return
  fi
  log_info "creating artifact registry repository ${AR_REPO}"
  gcloud artifacts repositories create "${AR_REPO}" \
    --project="${PROJECT_ID}" --location="${REGION}" \
    --repository-format=docker \
    --description="test-api container images"
}

# Least privilege for each deployer: run.admin to replace services, writer on
# this one repository only (not project-wide), and actAs on its own runtime SA
# only, so a deployer cannot launch a service as any other identity.
grant_deployer_roles() {
  local env deployer
  for env in "${ENVIRONMENTS[@]}"; do
    deployer="serviceAccount:$(sa_email_for "${env}")"
    log_info "granting deploy roles to $(sa_id_for "${env}")"
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --role="roles/run.admin" \
      --member="${deployer}" \
      --condition=None >/dev/null
    gcloud artifacts repositories add-iam-policy-binding "${AR_REPO}" \
      --project="${PROJECT_ID}" --location="${REGION}" \
      --role="roles/artifactregistry.writer" \
      --member="${deployer}" >/dev/null
    gcloud iam service-accounts add-iam-policy-binding \
      "$(runtime_sa_email_for "${env}")" \
      --project="${PROJECT_ID}" \
      --role="roles/iam.serviceAccountUser" \
      --member="${deployer}" \
      --condition=None >/dev/null
  done
}

# Each SA trusts ONLY jobs running in its own GitHub Environment, so the
# production SA is reachable only from a job that declares
# `environment: production`, not from any workflow in the repo. Uses the
# project NUMBER, never a wildcard pool.
# Args: $1 project number.
bind_workload_identity() {
  local project_number="$1"
  local env member
  for env in "${ENVIRONMENTS[@]}"; do
    member="principalSet://iam.googleapis.com/projects/${project_number}"
    member+="/locations/global/workloadIdentityPools/${POOL_ID}"
    member+="/attribute.environment/${env}"
    log_info "binding workloadIdentityUser on $(sa_id_for "${env}")"
    gcloud iam service-accounts add-iam-policy-binding \
      "$(sa_email_for "${env}")" \
      --project="${PROJECT_ID}" \
      --role="roles/iam.workloadIdentityUser" \
      --member="${member}" \
      --condition=None >/dev/null
  done
}

# Business secrets live in Secret Manager, never in GitHub secrets. Access is
# granted on the secret itself, not project-wide.
create_secret() {
  local env
  if gcloud secrets describe "${SECRET_ID}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    log_info "secret ${SECRET_ID} already exists"
  else
    log_info "creating secret ${SECRET_ID} with a random value"
    openssl rand -hex 24 | tr -d '\n' | gcloud secrets create "${SECRET_ID}" \
      --project="${PROJECT_ID}" \
      --replication-policy=user-managed \
      --locations="${REGION}" \
      --data-file=-
  fi
  for env in "${ENVIRONMENTS[@]}"; do
    gcloud secrets add-iam-policy-binding "${SECRET_ID}" \
      --project="${PROJECT_ID}" \
      --role="roles/secretmanager.secretAccessor" \
      --member="serviceAccount:$(sa_email_for "${env}")" \
      --condition=None >/dev/null
  done
}

# Prints the GitHub variables to set. Nothing is executed against GitHub.
# Args: $1 project number.
print_github_vars() {
  local project_number="$1"
  local provider env repo
  provider="projects/${project_number}/locations/global"
  provider+="/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
  repo="${GITHUB_OWNER}/${GITHUB_REPO}"

  log_info "done; IAM propagation can take 2-7 minutes before auth works"
  echo
  echo "GitHub variables (vars, not secrets):"
  echo "  WIF_PROVIDER (repository) = ${provider}"
  for env in "${ENVIRONMENTS[@]}"; do
    echo "  DEPLOY_SA (environment ${env}) = $(sa_email_for "${env}")"
  done
  echo
  echo "Commands to run (printed only):"
  echo "  gh variable set WIF_PROVIDER --repo ${repo} --body '${provider}'"
  for env in "${ENVIRONMENTS[@]}"; do
    echo "  gh variable set DEPLOY_SA --repo ${repo} --env ${env}" \
      "--body '$(sa_email_for "${env}")'"
  done
}

main() {
  local project_number
  require_commands
  validate_sa_ids
  enable_apis
  project_number="$(gcloud projects describe "${PROJECT_ID}" \
    --format='value(projectNumber)')"
  if [[ -z "${project_number}" ]]; then
    log_error "could not resolve project number for ${PROJECT_ID}"
    exit 1
  fi
  log_info "project ${PROJECT_ID} number ${project_number}"
  create_pool
  create_provider
  create_service_accounts
  bind_workload_identity "${project_number}"
  create_artifact_registry
  grant_deployer_roles
  create_secret
  print_github_vars "${project_number}"
}

main "$@"
