#!/bin/bash
# -*- coding: utf-8 -*-
# Language: Bash / Shell
# GitHub side of the keyless auth setup: creates the production Environment
# (no required reviewers, so a push to main deploys unattended) and sets the WIF_PROVIDER and DEPLOY_SA
# variables that setup-wif.sh prints. Run it after setup-wif.sh.
# Re-runnable: PUT and `gh variable set` overwrite. See infra/gcp/README.md.

set -euo pipefail
IFS=$'\n\t'

# Configuration (override via environment variables). Must match the values
# used for setup-wif.sh.
PROJECT_ID="${PROJECT_ID:-lorenz-1234}"
GITHUB_OWNER="${GITHUB_OWNER:-lorenzo485}"
GITHUB_REPO="${GITHUB_REPO:-test-api}"
POOL_ID="${POOL_ID:-github}"
PROVIDER_ID="${PROVIDER_ID:-github-actions}"
# gh account with admin on the repo. gh may be logged in to several accounts
# (e.g. a work one as active); this one is used for this script only.
GH_USER="${GH_USER:-${GITHUB_OWNER}}"
ENVIRONMENTS=(production)

readonly PROJECT_ID GITHUB_OWNER GITHUB_REPO POOL_ID PROVIDER_ID GH_USER
readonly ENVIRONMENTS
REPO="${GITHUB_OWNER}/${GITHUB_REPO}"
readonly REPO

log_info()  { echo "[INFO]  $(date -Iseconds) $*"; }
log_error() { echo "[ERROR] $(date -Iseconds) $*" >&2; }

# Fails early when a required CLI is missing or gh is not logged in.
require_commands() {
  local cmd
  for cmd in gcloud gh; do
    command -v "${cmd}" >/dev/null 2>&1 || {
      log_error "required command not found: ${cmd}"
      exit 1
    }
  done
}

# Pins every gh call in this process to GH_USER via GH_TOKEN, without
# switching the globally active gh account, then checks it is a repo admin
# (needed to create Environments and set variables).
use_gh_account() {
  local token is_admin
  token="$(gh auth token --user "${GH_USER}" 2>/dev/null)" || {
    log_error "gh has no login for ${GH_USER}; add it with:" \
      "gh auth login -s repo (then gh auth switch back if needed)"
    exit 1
  }
  export GH_TOKEN="${token}"
  is_admin="$(gh api "repos/${REPO}" --jq .permissions.admin 2>/dev/null)" \
    || is_admin="false"
  if [[ "${is_admin}" != "true" ]]; then
    log_error "${GH_USER} is not an admin of ${REPO}"
    exit 1
  fi
  log_info "using gh account ${GH_USER}"
}

# Creates or updates one Environment. The empty reviewers list also clears a
# reviewer left over from an earlier setup. Only main may deploy to it, so no
# other branch can get an OIDC token for the deployer SA.
# Args: $1 environment name.
create_environment() {
  local env="$1"
  gh api -X PUT "repos/${REPO}/environments/${env}" --input - >/dev/null \
    <<<'{"reviewers": [], "deployment_branch_policy":
         {"protected_branches": false, "custom_branch_policies": true}}'
  if [[ "$(gh api "repos/${REPO}/environments/${env}/deployment-branch-policies" \
      --jq 'any(.branch_policies[]; .name == "main")')" != "true" ]]; then
    gh api -X POST \
      "repos/${REPO}/environments/${env}/deployment-branch-policies" \
      -f name=main -f type=branch >/dev/null
  fi
  log_info "environment ${env} ready (deploys from main only)"
}

main() {
  local project_number provider env
  require_commands
  use_gh_account

  project_number="$(gcloud projects describe "${PROJECT_ID}" \
    --format='value(projectNumber)')"
  if [[ -z "${project_number}" ]]; then
    log_error "could not resolve project number for ${PROJECT_ID}"
    exit 1
  fi
  provider="projects/${project_number}/locations/global"
  provider+="/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

  for env in "${ENVIRONMENTS[@]}"; do
    create_environment "${env}"
  done

  gh variable set WIF_PROVIDER --repo "${REPO}" --body "${provider}"
  log_info "repository variable WIF_PROVIDER = ${provider}"
  for env in "${ENVIRONMENTS[@]}"; do
    gh variable set DEPLOY_SA --repo "${REPO}" --env "${env}" \
      --body "sa-deployer-${env}@${PROJECT_ID}.iam.gserviceaccount.com"
    log_info "environment ${env} variable DEPLOY_SA set"
  done
  log_info "done; verify by running the pipeline workflow"
}

main "$@"
