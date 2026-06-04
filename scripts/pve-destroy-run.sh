#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[destroy] %s\n' "$*"; }
warn() { printf '[destroy] WARN: %s\n' "$*" >&2; }
die() { printf '[destroy] ERROR: %s\n' "$*" >&2; exit 1; }
run_pve() {
  if (( EUID == 0 )); then
    "$@"
  else
    sudo -n "$@"
  fi
}

sdn_name_is_generated() {
  local name="$1"
  [[ "$name" =~ ^[a-z][a-z0-9]?[0-9]{6}$ ]]
}

sdn_apply() {
  log "Applying Proxmox SDN configuration"
  run_pve pvesh set /cluster/sdn
}

RUN_ID="${1:-}"
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || die "usage: $0 RUN_ID"
REQUESTED_RUN_ID="$RUN_ID"
PVE_DESTROY_JOBS="${PVE_DESTROY_JOBS:-6}"
[[ "$PVE_DESTROY_JOBS" =~ ^[1-9][0-9]*$ ]] || die "PVE_DESTROY_JOBS must be a positive integer"
destroy_status_dir="$(mktemp -d)"
trap 'rm -rf "$destroy_status_dir"' EXIT
state_cmd() {
  if [[ -f artifacts/topology.json || -f artifacts/run-state.json ]]; then
    ./scripts/run-state.py "$@" --run-id "$REQUESTED_RUN_ID" || true
  fi
}
state_cmd phase destroy --status started

if [[ -f artifacts/topology.env ]]; then
  # shellcheck disable=SC1091
  source artifacts/topology.env
  [[ "${RUN_ID:-}" == "$REQUESTED_RUN_ID" ]] || die "topology RUN_ID does not match requested RUN_ID"
elif [[ ! -f artifacts/topology.json ]]; then
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

safe_status_name() {
  local name="$1"
  printf '%s' "${name//[^A-Za-z0-9_.-]/_}"
}

record_destroy_status() {
  local host="$1" status="$2" message="${3:-}" safe_host
  safe_host="$(safe_status_name "$host")"
  printf '%s\t%s\t%s\n' "$host" "$status" "$message" > "$destroy_status_dir/${safe_host}.status"
}

flush_destroy_statuses() {
  local status_file host status message
  for status_file in "$destroy_status_dir"/*.status; do
    [[ -e "$status_file" ]] || return 0
    IFS=$'\t' read -r host status message < "$status_file"
    if [[ "$status" == "unsafe" ]]; then
      state_cmd phase destroy --status unsafe --message "$message"
    else
      state_cmd vm "$host" "$status"
    fi
  done
}

destroy_one() {
  local host="$1" vmid="$2" expected_name="$3" actual_name
  if ! run_pve qm status "$vmid" >/dev/null 2>&1; then
    log "VMID $vmid missing; skipping"
    record_destroy_status "$host" missing
    return 0
  fi
  actual_name=$(run_pve qm config "$vmid" | awk -F': ' '/^name:/ {print $2}')
  if [[ "$actual_name" != "$expected_name" ]]; then
    warn "VMID $vmid name is $actual_name, expected $expected_name; refusing to destroy"
    record_destroy_status "$host" unsafe "VMID $vmid name mismatch: $actual_name != $expected_name"
    return 1
  fi
  log "Stopping $expected_name ($vmid)"
  run_pve qm stop "$vmid" --skiplock 1 || true
  log "Destroying $expected_name ($vmid)"
  run_pve qm destroy "$vmid" --purge 1 || true
  record_destroy_status "$host" destroyed
}

verify_destroy_targets() {
  local entry host vmid expected_name actual_name
  for entry in "${destroy_entries[@]}"; do
    IFS=$'\t' read -r host vmid expected_name <<<"$entry"
    if ! run_pve qm status "$vmid" >/dev/null 2>&1; then
      continue
    fi
    actual_name=$(run_pve qm config "$vmid" | awk -F': ' '/^name:/ {print $2}')
    if [[ "$actual_name" != "$expected_name" ]]; then
      state_cmd phase destroy --status unsafe --message "VMID $vmid name mismatch: $actual_name != $expected_name"
      die "unsafe VMID name mismatch for $vmid"
    fi
  done
}

wait_for_destroy_slot() {
  while (( $(jobs -pr | wc -l | tr -d '[:space:]') >= PVE_DESTROY_JOBS )); do
    sleep 0.2
  done
}

start_destroy_one() {
  wait_for_destroy_slot
  destroy_one "$@" &
  destroy_pids+=("$!")
}

log "Destroying VMs with $PVE_DESTROY_JOBS workers"
destroy_pids=()
if [[ -f artifacts/topology.json ]]; then
  mapfile -t destroy_entries < <(jq -r '.hosts[] | [.name, .vmid, .vm_name] | @tsv' artifacts/topology.json)
else
  destroy_entries=(
    $'client-a\t'"$CLIENT_A"$'\t'"$CLIENT_A_NAME"
    $'vtep-a\t'"$VTEP_A"$'\t'"$VTEP_A_NAME"
    $'vtep-b\t'"$VTEP_B"$'\t'"$VTEP_B_NAME"
    $'client-b\t'"$CLIENT_B"$'\t'"$CLIENT_B_NAME"
  )
fi
verify_destroy_targets
for entry in "${destroy_entries[@]}"; do
  IFS=$'\t' read -r host vmid vm_name <<<"$entry"
  start_destroy_one "$host" "$vmid" "$vm_name"
done
destroy_failed=0
for pid in "${destroy_pids[@]}"; do
  if ! wait "$pid"; then
    destroy_failed=1
  fi
done
flush_destroy_statuses
(( destroy_failed == 0 )) || die "one or more VM destroy workers failed"

if [[ "${NETWORK_MODE:-bridge}" == "qinq" && -f artifacts/topology.json ]]; then
  log "Deleting generated QinQ SDN VNets"
  while IFS= read -r vnet; do
    sdn_name_is_generated "$vnet" || die "refusing to delete unexpected SDN VNet name: $vnet"
    run_pve pvesh delete "/cluster/sdn/vnets/$vnet" || true
    state_cmd sdn vnet "$vnet" deleted
  done < <(jq -r '.networks[] | select(.vnet != "") | .vnet' artifacts/topology.json)
  zone=$(jq -r '.qinq.zone' artifacts/topology.json)
  log "Deleting generated QinQ SDN zone $zone"
  sdn_name_is_generated "$zone" || die "refusing to delete unexpected SDN zone name: $zone"
  run_pve pvesh delete "/cluster/sdn/zones/$zone" || true
  state_cmd sdn zone "$zone" deleted
  sdn_apply || warn "SDN apply failed after deletion"
elif [[ "${NETWORK_MODE:-bridge}" == "qinq" ]]; then
  if [[ -n "${LEFT_VNET:-}" || -n "${UNDERLAY_VNET:-}" || -n "${RIGHT_VNET:-}" || -n "${QINQ_ZONE:-}" ]]; then
    log "Deleting legacy generated QinQ SDN objects from topology.env"
    for sdn_name in "${LEFT_VNET:-}" "${UNDERLAY_VNET:-}" "${RIGHT_VNET:-}" "${QINQ_ZONE:-}"; do
      [[ -n "$sdn_name" ]] || die "missing generated QinQ SDN name in topology.env"
      sdn_name_is_generated "$sdn_name" || die "refusing to delete unexpected SDN object name: $sdn_name"
    done
    run_pve pvesh delete "/cluster/sdn/vnets/$LEFT_VNET" || true
    run_pve pvesh delete "/cluster/sdn/vnets/$UNDERLAY_VNET" || true
    run_pve pvesh delete "/cluster/sdn/vnets/$RIGHT_VNET" || true
    zone="$QINQ_ZONE"
    log "Deleting generated QinQ SDN zone $zone"
    run_pve pvesh delete "/cluster/sdn/zones/$zone" || true
    sdn_apply || warn "SDN apply failed after deletion"
  else
    log "No rendered topology or legacy QinQ names; skipping SDN cleanup"
  fi
fi

state_cmd phase destroy --status completed
log "Destroy complete"
