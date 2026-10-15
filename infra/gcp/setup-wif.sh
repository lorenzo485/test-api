#!/bin/bash
# -*- coding: utf-8 -*-
# Language: Bash / Shell
# One-time GCP setup for keyless GitHub Actions auth (Workload Identity
# Federation), one deployer service account per GitHub Environment, and a
# demo secret in Secret Manager. Re-runnable: every create is guarded by a
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
ENVIRONMENTS=(development staging production)

readonly PROJECT_ID REGION GITHUB_OWNER GITHUB_REPO POOL_ID PROVIDER_ID
readonly SECRET_ID ENVIRONMENTS

log_info()  { echo "[INFO]  $(date -Iseconds) $*"; }
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
    id="$(sa_id_for "${env}")"
    if (( ${#id} > 30 )); then
      log_error "service account id too long (${#id} > 30): ${id}"
      exit 2
    fi
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

create_service_accounts() {
  local env id
  for env in "${ENVIRONMENTS[@]}"; do
    id="$(sa_id_for "${env}")"
    if gcloud iam service-accounts describe "$(sa_email_for "${env}")" \
      --project="${PROJECT_ID}" >/dev/null 2>&1; then
      log_info "service account ${id} already exists"
      continue
    fi
    log_info "creating service account ${id}"
    gcloud iam service-accounts create "${id}" \
      --project="${PROJECT_ID}" \
      --display-name="GitHub deployer (${env})"
  done
}

# Each SA trusts ONLY jobs running in its own GitHub Environment. The deck
# binds on attribute.ref == refs/heads/main, which breaks staging-branch and
# tag deploys; binding on the environment instead means the production SA is
# reachable only after the production Environment's required reviewers
# approve. Uses the project NUMBER, never a wildcard pool.
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
  create_secret
  print_github_vars "${project_number}"
}

main "$@"
