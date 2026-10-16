#!/bin/bash
# -*- coding: utf-8 -*-
# Language: Bash / Shell
# Protects the default branch with a repository ruleset: changes land only
# through a pull request, and the lint, typecheck, and test jobs from
# .github/workflows/ci.yml plus the sca and dependency-review jobs from
# .github/workflows/security.yml must pass before the merge button is enabled.
# Without this, CI only reports a red check; it does not block the merge.
# Re-runnable: updates the ruleset in place when one with RULESET_NAME exists.

set -euo pipefail
IFS=$'\n\t'

# Configuration (override via environment variables).
GITHUB_OWNER="${GITHUB_OWNER:-lorenzo485}"
GITHUB_REPO="${GITHUB_REPO:-test-api}"
# gh account with admin on the repo. gh may be logged in to several accounts
# (e.g. a work one as active); this one is used for this script only.
GH_USER="${GH_USER:-${GITHUB_OWNER}}"
RULESET_NAME="${RULESET_NAME:-protect-main}"
# Must match the job ids in .github/workflows/ci.yml and security.yml.
REQUIRED_CHECKS=(lint typecheck test sca dependency-review)
# App id of GitHub Actions: only Actions may report the required checks.
GITHUB_ACTIONS_APP_ID=15368

readonly GITHUB_OWNER GITHUB_REPO GH_USER RULESET_NAME REQUIRED_CHECKS
readonly GITHUB_ACTIONS_APP_ID
REPO="${GITHUB_OWNER}/${GITHUB_REPO}"
readonly REPO

log_info()  { echo "[INFO]  $(date -Iseconds) $*"; }
log_error() { echo "[ERROR] $(date -Iseconds) $*" >&2; }

# Fails early when a required CLI is missing.
require_commands() {
  local cmd
  for cmd in gh jq; do
    command -v "${cmd}" >/dev/null 2>&1 || {
      log_error "required command not found: ${cmd}"
      exit 1
    }
  done
}

# Pins every gh call in this process to GH_USER via GH_TOKEN, without
# switching the globally active gh account, then checks it is a repo admin
# (needed to manage rulesets).
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

# Prints the ruleset JSON body. Zero approvals because a solo maintainer
# cannot approve their own PR; the PR rule still forbids direct pushes.
ruleset_body() {
  local checks
  checks="$(printf '%s\n' "${REQUIRED_CHECKS[@]}" \
    | jq -R --argjson app "${GITHUB_ACTIONS_APP_ID}" \
      '{context: ., integration_id: $app}' | jq -s .)"
  jq -n --arg name "${RULESET_NAME}" --argjson checks "${checks}" '{
    name: $name,
    target: "branch",
    enforcement: "active",
    conditions: {ref_name: {include: ["~DEFAULT_BRANCH"], exclude: []}},
    rules: [
      {type: "deletion"},
      {type: "non_fast_forward"},
      {type: "pull_request", parameters: {
        required_approving_review_count: 0,
        dismiss_stale_reviews_on_push: false,
        require_code_owner_review: false,
        require_last_push_approval: false,
        required_review_thread_resolution: false
      }},
      {type: "required_status_checks", parameters: {
        strict_required_status_checks_policy: false,
        required_status_checks: $checks
      }}
    ]
  }'
}

main() {
  require_commands
  use_gh_account

  local existing_id
  existing_id="$(gh api "repos/${REPO}/rulesets" \
    --jq ".[] | select(.name == \"${RULESET_NAME}\") | .id")"

  if [[ -n "${existing_id}" ]]; then
    ruleset_body | gh api -X PUT "repos/${REPO}/rulesets/${existing_id}" \
      --input - >/dev/null
    log_info "ruleset ${RULESET_NAME} (${existing_id}) updated"
  else
    ruleset_body | gh api -X POST "repos/${REPO}/rulesets" \
      --input - >/dev/null
    log_info "ruleset ${RULESET_NAME} created"
  fi
  local IFS=' '
  log_info "required checks on default branch: ${REQUIRED_CHECKS[*]}"
}

main "$@"
