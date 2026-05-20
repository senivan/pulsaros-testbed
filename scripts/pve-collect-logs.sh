#!/usr/bin/env bash
set -euo pipefail

log() { printf '[collect] %s\n' "$*"; }
warn() { printf '[collect] WARN: %s\n' "$*" >&2; }
die() { printf '[collect] ERROR: %s\n' "$*" >&2; exit 1; }

RUN_ID="${1:-}"
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || die "usage: $0 RUN_ID"
REQUESTED_RUN_ID="$RUN_ID"
mkdir -p logs pcaps artifacts junit

if [[ ! -f artifacts/topology.env ]]; then
  warn "artifacts/topology.env not found; nothing to collect"
  exit 0
fi
# shellcheck disable=SC1091
source artifacts/topology.env
[[ "${RUN_ID:-}" == "$REQUESTED_RUN_ID" ]] || die "topology RUN_ID does not match requested RUN_ID"

SSH_USER="${ANSIBLE_USER:-pulsar}"
SSH_KEY="${ANSIBLE_SSH_PRIVATE_KEY_FILE:-$HOME/.ssh/pulsaros-testbed}"
SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10)

collect_cmd() {
  local host="$1" ip="$2" suffix="$3" cmd="$4"
  if [[ -z "$ip" ]]; then
    warn "$host has no IP; skipping $suffix"
    return 0
  fi
  log "Collecting $suffix from $host"
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$ip" "$cmd" > "logs/${host}-${suffix}.log" 2>&1 || warn "failed to collect $suffix from $host"
}

copy_pcap() {
  local host="$1" ip="$2" remote="$3" local_path="$4"
  [[ -n "$ip" ]] || return 0
  log "Collecting pcap from $host"
  scp "${SSH_OPTS[@]}" "$SSH_USER@$ip:$remote" "$local_path" >/dev/null 2>&1 || warn "pcap not available on $host"
}

for entry in \
  "client-a:${CLIENT_A_IP:-}" \
  "client-b:${CLIENT_B_IP:-}" \
  "vtep-a:${VTEP_A_IP:-}" \
  "vtep-b:${VTEP_B_IP:-}"; do
  host="${entry%%:*}"
  ip="${entry#*:}"
  collect_cmd "$host" "$ip" dmesg "sudo dmesg || dmesg"
  collect_cmd "$host" "$ip" journal "sudo journalctl -b --no-pager || journalctl -b --no-pager"
  collect_cmd "$host" "$ip" ip-link "ip link"
  collect_cmd "$host" "$ip" ip-addr "ip addr"
  collect_cmd "$host" "$ip" uname "uname -a"
done

copy_pcap vtep-a "${VTEP_A_IP:-}" /tmp/pulsaros-testbed/vtep-a-underlay.pcap pcaps/vtep-a-underlay.pcap
copy_pcap vtep-b "${VTEP_B_IP:-}" /tmp/pulsaros-testbed/vtep-b-underlay.pcap pcaps/vtep-b-underlay.pcap

log "Collection complete"
