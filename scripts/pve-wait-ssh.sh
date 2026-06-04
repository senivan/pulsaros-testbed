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
./scripts/run-state.py phase wait-ssh --status started --run-id "$RUN_ID"
WAIT_TMP=""
finish_wait_state() {
  local rc=$?
  if [[ -n "${WAIT_TMP:-}" ]]; then
    rm -rf "$WAIT_TMP"
  fi
  if (( rc != 0 )); then
    ./scripts/run-state.py phase wait-ssh --status failed --message "pve-wait-ssh.sh exited with $rc" --run-id "$RUN_ID" || true
  fi
  exit "$rc"
}
trap finish_wait_state EXIT

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

known_ip() {
  local host="$1"
  jq -r --arg host "$host" '.hosts[$host].management_ip // ""' artifacts/topology.json
}

wait_for_host() {
  local host="$1" vmid="$2" ip
  ip=$(known_ip "$host")
  if [[ -n "$ip" ]] && ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$SSH_USER@$ip" true >/dev/null 2>&1; then
    log "$host SSH reachable at known management IP $ip"
  else
    ip=$(wait_for_ip "$host" "$vmid")
    wait_for_ssh "$host" "$ip"
  fi
  printf '%s=%s\n' "$host" "$ip" > "$WAIT_TMP/$host.ip"
}

mapfile -t host_entries < <(jq -r '.hosts | to_entries[] | {name: .key, vmid: .value.vmid} | @base64' artifacts/topology.json)
log "Waiting for ${#host_entries[@]} topology hosts"
WAIT_TMP=$(mktemp -d "${TMPDIR:-/tmp}/pulsaros-wait-ssh.XXXXXX")
host_ip_args=()
wait_pids=()
wait_hosts=()
for entry in "${host_entries[@]}"; do
  host=$(printf '%s' "$entry" | base64 -d | jq -r '.name')
  vmid=$(printf '%s' "$entry" | base64 -d | jq -r '.vmid')
  [[ -n "$host" ]] || continue
  wait_for_host "$host" "$vmid" &
  wait_pids+=("$!")
  wait_hosts+=("$host")
done

failed=0
for index in "${!wait_pids[@]}"; do
  if ! wait "${wait_pids[$index]}"; then
    log "Host wait failed for ${wait_hosts[$index]}"
    failed=1
  fi
done
if (( failed != 0 )); then
  die "one or more hosts did not become SSH-ready"
fi

for entry in "${host_entries[@]}"; do
  host=$(printf '%s' "$entry" | base64 -d | jq -r '.name')
  [[ -n "$host" ]] || continue
  [[ -f "$WAIT_TMP/$host.ip" ]] || die "missing wait result for $host"
  host_ip_args+=("$(cat "$WAIT_TMP/$host.ip")")
done
./scripts/render-topology.py update-ips "${host_ip_args[@]}"
./scripts/run-state.py sync-topology --run-id "$RUN_ID"
for arg in "${host_ip_args[@]}"; do
  host="${arg%%=*}"
  ip="${arg#*=}"
  ./scripts/run-state.py vm "$host" ssh-ready --management-ip "$ip" --run-id "$RUN_ID"
done
./scripts/run-state.py phase wait-ssh --status completed --run-id "$RUN_ID"
trap - EXIT
rm -rf "$WAIT_TMP"
log "Updated artifacts/topology.json and artifacts/topology.env with management IPs"
