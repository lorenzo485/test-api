#!/bin/bash
# -*- coding: utf-8 -*-
# Language: Bash / Shell
# Load generator for the SLO lab: hammers GET /checkout with CONCURRENCY
# workers until Ctrl+C and prints, every REPORT_SEC, the same two SLIs the
# SLOs measure: share of 5xx responses and share of responses slower than 1 s.

set -euo pipefail
IFS=$'\n\t'

# Configuration (override via environment variables).
BASE_URL="${BASE_URL:-https://test-api-production-70150279631.us-east1.run.app}"
CONCURRENCY="${CONCURRENCY:-5}"
REPORT_SEC="${REPORT_SEC:-30}"
# Above the slowest injected delay, so a slow request is measured as slow
# instead of being cut off (a cut-off request is logged as 499, not 5xx).
TIMEOUT_SEC="${TIMEOUT_SEC:-10}"
# Identity tokens expire after 1 hour; refresh well before that.
TOKEN_REFRESH_SEC="${TOKEN_REFRESH_SEC:-1800}"

readonly BASE_URL CONCURRENCY REPORT_SEC TIMEOUT_SEC TOKEN_REFRESH_SEC

log_info() { echo "[INFO]  $(date -Iseconds) $*"; }
log_error() { echo "[ERROR] $(date -Iseconds) $*" >&2; }

WORK_DIR="$(mktemp -d)"
RESULTS="${WORK_DIR}/results"
TOKEN_FILE="${WORK_DIR}/token"
readonly WORK_DIR RESULTS TOKEN_FILE

# Prints request count, 5xx share and >1 s share for a results file.
# Args: $1 file with "<http_code> <seconds>" lines.
summarize() {
  awk '
    { total++; if ($1 >= 500) errors++; if ($2 > 1) slow++ }
    END {
      if (total == 0) { print "no requests yet"; exit }
      printf "requests=%d  5xx=%.2f%%  slow(>1s)=%.2f%%\n",
        total, 100 * errors / total, 100 * slow / total
    }' "$1"
}

refresh_token() {
  gcloud auth print-identity-token >"${TOKEN_FILE}"
}

# Sends requests forever, appending one result line per request.
worker() {
  while true; do
    curl -sS -o /dev/null --max-time "${TIMEOUT_SEC}" \
      -w '%{http_code} %{time_total}\n' \
      -H "Authorization: Bearer $(<"${TOKEN_FILE}")" \
      "${BASE_URL}/checkout" >>"${RESULTS}" 2>/dev/null || true
  done
}

cleanup() {
  trap - EXIT INT TERM
  exec 2>/dev/null # silence bash's "Terminated" notice for each worker
  jobs -p | xargs kill || true
  wait || true
  log_info "final: $(summarize "${RESULTS}")"
  rm -rf "${WORK_DIR}"
  exit 0
}

main() {
  command -v gcloud >/dev/null 2>&1 || { log_error "gcloud not found"; exit 1; }
  touch "${RESULTS}"
  refresh_token
  trap cleanup EXIT INT TERM

  log_info "GET ${BASE_URL}/checkout with ${CONCURRENCY} workers (Ctrl+C stops)"
  for _ in $(seq "${CONCURRENCY}"); do
    worker &
  done

  local last_refresh="${SECONDS}" window_start=0 lines
  while true; do
    # Waiting on a background sleep lets Ctrl+C run cleanup immediately.
    sleep "${REPORT_SEC}" &
    wait "$!"
    # Report the last window only, so a change in chaos knobs shows up fast.
    lines="$(wc -l <"${RESULTS}")"
    log_info "last ${REPORT_SEC}s: $(tail -n "+$((window_start + 1))" \
      "${RESULTS}" | head -n "$((lines - window_start))" | summarize /dev/stdin)"
    window_start="${lines}"
    if ((SECONDS - last_refresh >= TOKEN_REFRESH_SEC)); then
      refresh_token
      last_refresh="${SECONDS}"
    fi
  done
}

main "$@"
