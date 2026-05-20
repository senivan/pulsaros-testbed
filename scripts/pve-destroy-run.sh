#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[destroy] %s\n' "$*"; }
warn() { printf '[destroy] WARN: %s\n' "$*" >&2; }
die() { printf '[destroy] ERROR: %s\n' "$*" >&2; exit 1; }

RUN_ID="${1:-}"
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || die "usage: $0 RUN_ID"
REQUESTED_RUN_ID="$RUN_ID"

if [[ -f artifacts/topology.env ]]; then
  # shellcheck disable=SC1091
  source artifacts/topology.env
  [[ "${RUN_ID:-}" == "$REQUESTED_RUN_ID" ]] || die "topology RUN_ID does not match requested RUN_ID"
else
  BASE=$(( 200000 + REQUESTED_RUN_ID % 50000 ))
  CLIENT_A=$((BASE + 1))
  VTEP_A=$((BASE + 2))
  VTEP_B=$((BASE + 3))
  CLIENT_B=$((BASE + 4))
  CLIENT_A_NAME="pulsar-${REQUESTED_RUN_ID}-client-a"
  VTEP_A_NAME="pulsar-${REQUESTED_RUN_ID}-vtep-a"
  VTEP_B_NAME="pulsar-${REQUESTED_RUN_ID}-vtep-b"
  CLIENT_B_NAME="pulsar-${REQUESTED_RUN_ID}-client-b"
fi

destroy_one() {
  local vmid="$1" expected_name="$2" actual_name
  if ! qm status "$vmid" >/dev/null 2>&1; then
    log "VMID $vmid missing; skipping"
    return 0
  fi
  actual_name=$(qm config "$vmid" | awk -F': ' '/^name:/ {print $2}')
  if [[ "$actual_name" != "$expected_name" ]]; then
    warn "VMID $vmid name is $actual_name, expected $expected_name; refusing to destroy"
    return 0
  fi
  log "Stopping $expected_name ($vmid)"
  qm stop "$vmid" --skiplock 1 || true
  log "Destroying $expected_name ($vmid)"
  qm destroy "$vmid" --purge 1 || true
}

destroy_one "$CLIENT_A" "$CLIENT_A_NAME"
destroy_one "$VTEP_A" "$VTEP_A_NAME"
destroy_one "$VTEP_B" "$VTEP_B_NAME"
destroy_one "$CLIENT_B" "$CLIENT_B_NAME"
log "Destroy complete"
