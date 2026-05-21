#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[wait-ssh] %s\n' "$*" >&2; }
die() { printf '[wait-ssh] ERROR: %s\n' "$*" >&2; exit 1; }
run_pve() {
  if (( EUID == 0 )); then
    "$@"
  else
    sudo -n "$@"
  fi
}

REQUESTED_RUN_ID="${1:-}"
[[ "$REQUESTED_RUN_ID" =~ ^[0-9]+$ ]] || die "usage: $0 RUN_ID"
[[ -f artifacts/topology.json ]] || die "artifacts/topology.json not found"
# shellcheck disable=SC1091
source artifacts/topology.env
[[ "${RUN_ID:-}" == "$REQUESTED_RUN_ID" ]] || die "topology RUN_ID does not match requested RUN_ID"

SSH_USER="${ANSIBLE_USER:-pulsar}"
SSH_KEY="${ANSIBLE_SSH_PRIVATE_KEY_FILE:-$HOME/.ssh/pulsaros-testbed}"
TIMEOUT_SECONDS="${PVE_SSH_TIMEOUT_SECONDS:-600}"
SLEEP_SECONDS=5

guest_ip() {
  local vmid="$1" json ip
  json=$(run_pve qm guest cmd "$vmid" network-get-interfaces 2>/dev/null || true)
  [[ -n "$json" ]] || return 1
  ip=$(jq -r '
    .[]
    | ."ip-addresses"? // []
    | .[]
    | select(."ip-address-type" == "ipv4")
    | ."ip-address"
    | select(startswith("127.") | not)
    | select(startswith("169.254.") | not)
  ' <<<"$json" | head -n1)
  [[ -n "$ip" && "$ip" != "null" ]] || return 1
  printf '%s\n' "$ip"
}

wait_for_ip() {
  local label="$1" vmid="$2" deadline ip
  deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if ip=$(guest_ip "$vmid"); then
      log "$label management IP: $ip"
      printf '%s\n' "$ip"
      return 0
    fi
    log "Waiting for qemu guest agent IP on $label"
    sleep "$SLEEP_SECONDS"
  done
  die "timed out waiting for qemu guest agent IP on $label"
}

wait_for_ssh() {
  local label="$1" ip="$2" deadline
  deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$SSH_USER@$ip" true >/dev/null 2>&1; then
      log "$label SSH reachable"
      return 0
    fi
    log "Waiting for SSH on $label ($ip)"
    sleep "$SLEEP_SECONDS"
  done
  die "timed out waiting for SSH on $label ($ip)"
}

host_list=$(jq -r '.hosts[] | [.name, .vmid] | @tsv' artifacts/topology.json)
host_ip_args=()
while IFS=$'\t' read -r host vmid; do
  [[ -n "$host" ]] || continue
  ip=$(wait_for_ip "$host" "$vmid")
  wait_for_ssh "$host" "$ip"
  host_ip_args+=("${host}=${ip}")
  ./scripts/render-topology.py update-ips "${host_ip_args[@]}"
done <<<"$host_list"

./scripts/render-topology.py update-ips "${host_ip_args[@]}"
log "Updated artifacts/topology.json and artifacts/topology.env with management IPs"
