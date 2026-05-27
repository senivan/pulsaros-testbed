#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[collect] %s\n' "$*"; }
warn() { printf '[collect] WARN: %s\n' "$*" >&2; }
die() { printf '[collect] ERROR: %s\n' "$*" >&2; exit 1; }
run_pve() {
  if (( EUID == 0 )); then
    "$@"
  else
    sudo -n "$@"
  fi
}

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
if [[ -f artifacts/topology.json || -f artifacts/run-state.json ]]; then
  ./scripts/run-state.py phase collect-logs --status started --run-id "$RUN_ID" || true
fi

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

collect_pve_cmd() {
  local host="$1" vmid="$2" suffix="$3"
  shift 3
  [[ -n "$vmid" ]] || return 0
  log "Collecting Proxmox $suffix from $host"
  run_pve "$@" > "logs/${host}-${suffix}.log" 2>&1 || warn "failed to collect Proxmox $suffix from $host"
}

collect_pve_serial() {
  local host="$1" vmid="$2"
  [[ -n "$vmid" ]] || return 0
  log "Collecting Proxmox serial console from $host"
  local serial_socket="/var/run/qemu-server/${vmid}.serial0"
  if (( EUID == 0 )); then
    if [[ -S "$serial_socket" ]]; then
      timeout 8 socat - "UNIX-CONNECT:${serial_socket}" > "logs/${host}-serial-console.log" 2>&1 || warn "failed to collect Proxmox serial console from $host"
    else
      timeout 8 qm terminal "$vmid" > "logs/${host}-serial-console.log" 2>&1 || warn "failed to collect Proxmox serial console from $host"
    fi
  elif command -v script >/dev/null 2>&1; then
    timeout 8 script -q -c "sudo -n qm terminal $vmid" /dev/null > "logs/${host}-serial-console.log" 2>&1 || warn "failed to collect Proxmox serial console from $host"
  else
    if sudo -n test -S "$serial_socket"; then
      timeout 8 sudo -n socat - "UNIX-CONNECT:${serial_socket}" > "logs/${host}-serial-console.log" 2>&1 || warn "failed to collect Proxmox serial console from $host"
    else
      timeout 8 sudo -n qm terminal "$vmid" > "logs/${host}-serial-console.log" 2>&1 || warn "failed to collect Proxmox serial console from $host"
    fi
  fi
}

collect_sdn_state() {
  [[ "${NETWORK_MODE:-bridge}" == "qinq" ]] || return 0
  [[ -f artifacts/topology.json ]] || return 0
  local zone
  zone=$(jq -r '.qinq.zone // ""' artifacts/topology.json)
  if [[ -n "$zone" ]]; then
    log "Collecting Proxmox SDN zone $zone"
    run_pve pvesh get "/cluster/sdn/zones/$zone" > "logs/sdn-zone-${zone}.log" 2>&1 || warn "failed to collect SDN zone $zone"
  fi
  while IFS= read -r vnet; do
    [[ -n "$vnet" ]] || continue
    log "Collecting Proxmox SDN VNet $vnet"
    run_pve pvesh get "/cluster/sdn/vnets/$vnet" > "logs/sdn-vnet-${vnet}.log" 2>&1 || warn "failed to collect SDN VNet $vnet"
    if [[ -d "/sys/class/net/$vnet" ]]; then
      ip -d link show "$vnet" > "logs/sdn-link-${vnet}.log" 2>&1 || warn "failed to collect link state for $vnet"
    fi
  done < <(jq -r '.networks[] | select(.vnet != "") | .vnet' artifacts/topology.json)
}

copy_pcap() {
  local host="$1" ip="$2" remote="$3" local_path="$4"
  [[ -n "$ip" ]] || return 0
  log "Collecting pcap from $host"
  scp "${SSH_OPTS[@]}" "$SSH_USER@$ip:$remote" "$local_path" >/dev/null 2>&1 || warn "pcap not available on $host"
}

collect_guest_agent_cmd() {
  local host="$1" vmid="$2" suffix="$3" cmd="$4"
  [[ -n "$vmid" ]] || return 0
  log "Collecting guest-agent $suffix from $host"
  local log_path="logs/${host}-guest-agent-${suffix}.log"
  local exec_output pid status
  if ! exec_output="$(run_pve qm guest exec "$vmid" -- /bin/sh -lc "$cmd" 2>&1)"; then
    printf '%s\n' "$exec_output" > "$log_path"
    warn "failed to start guest-agent $suffix collection from $host"
    return 0
  fi

  {
    printf 'guest-exec:\n%s\n' "$exec_output"
  } > "$log_path"

  pid="$(printf '%s\n' "$exec_output" | sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n1)"
  if [[ -z "$pid" ]]; then
    warn "guest-agent $suffix collection from $host did not return a pid"
    return 0
  fi

  for _ in {1..20}; do
    status="$(run_pve qm guest exec-status "$vmid" "$pid" 2>&1 || true)"
    {
      printf '\nguest-exec-status:\n%s\n' "$status"
      if command -v jq >/dev/null 2>&1 && command -v base64 >/dev/null 2>&1; then
        printf '\nstdout:\n'
        printf '%s\n' "$status" | jq -r '."out-data" // empty' 2>/dev/null | base64 -d 2>/dev/null || true
        printf '\nstderr:\n'
        printf '%s\n' "$status" | jq -r '."err-data" // empty' 2>/dev/null | base64 -d 2>/dev/null || true
      fi
    } >> "$log_path"
    if grep -Eq '"exited"[[:space:]]*:[[:space:]]*(true|1)' <<<"$status"; then
      return 0
    fi
    sleep 1
  done
  warn "guest-agent $suffix collection from $host did not finish before timeout"
}

if [[ -f artifacts/topology.json ]]; then
  mapfile -t host_entries < <(jq -r '.hosts[] | @base64' artifacts/topology.json)
else
  mapfile -t host_entries < <(jq -n -r \
    --arg ca_ip "${CLIENT_A_IP:-}" --arg ca "${CLIENT_A:-}" \
    --arg cb_ip "${CLIENT_B_IP:-}" --arg cb "${CLIENT_B:-}" \
    --arg va_ip "${VTEP_A_IP:-}" --arg va "${VTEP_A:-}" \
    --arg vb_ip "${VTEP_B_IP:-}" --arg vb "${VTEP_B:-}" \
    '[
      {name:"client-a", management_ip:$ca_ip, vmid:$ca},
      {name:"client-b", management_ip:$cb_ip, vmid:$cb},
      {name:"vtep-a", management_ip:$va_ip, vmid:$va},
      {name:"vtep-b", management_ip:$vb_ip, vmid:$vb}
    ][] | @base64')
fi

log "Collecting from ${#host_entries[@]} topology hosts"
collect_sdn_state
for entry in "${host_entries[@]}"; do
  [[ -n "$entry" ]] || continue
  host=$(printf '%s' "$entry" | base64 -d | jq -r '.name')
  ip=$(printf '%s' "$entry" | base64 -d | jq -r '.management_ip // ""')
  vmid=$(printf '%s' "$entry" | base64 -d | jq -r '.vmid // ""')
  [[ -n "$host" ]] || continue
  collect_pve_cmd "$host" "$vmid" qm-status qm status "$vmid" --verbose
  collect_pve_cmd "$host" "$vmid" qm-config qm config "$vmid"
  collect_pve_serial "$host" "$vmid"
  collect_guest_agent_cmd "$host" "$vmid" ping "true"
  collect_guest_agent_cmd "$host" "$vmid" ip-addr "ip addr || true"
  collect_guest_agent_cmd "$host" "$vmid" uname "uname -a || true"
  collect_guest_agent_cmd "$host" "$vmid" journal "journalctl -b --no-pager | tail -n 300 || true"
  collect_cmd "$host" "$ip" dmesg "sudo dmesg || dmesg"
  collect_cmd "$host" "$ip" journal "sudo journalctl -b --no-pager || journalctl -b --no-pager"
  collect_cmd "$host" "$ip" ip-link "ip link"
  collect_cmd "$host" "$ip" ip-addr "ip addr"
  collect_cmd "$host" "$ip" uname "uname -a"
  collect_cmd "$host" "$ip" kernel-rpms "rpm -qa 'kernel*' | sort || true"
  collect_cmd "$host" "$ip" kernel-boot "sudo grubby --info=DEFAULT || true; findmnt / || true; cat /proc/cmdline || true"
  collect_cmd "$host" "$ip" testbed-tmp "sudo sh -c 'ls -la /tmp/pulsaros-testbed 2>/dev/null || true; for f in /tmp/pulsaros-testbed/*.log; do [ -f \"\$f\" ] || continue; echo === \"\$f\"; cat \"\$f\"; done'"
done

if [[ -n "${VTEP_A_IP:-}" ]]; then
  copy_pcap vtep-a "${VTEP_A_IP:-}" /tmp/pulsaros-testbed/vtep-a-underlay.pcap pcaps/vtep-a-underlay.pcap
fi
if [[ -n "${VTEP_B_IP:-}" ]]; then
  copy_pcap vtep-b "${VTEP_B_IP:-}" /tmp/pulsaros-testbed/vtep-b-underlay.pcap pcaps/vtep-b-underlay.pcap
fi

if [[ -f artifacts/topology.json || -f artifacts/run-state.json ]]; then
  ./scripts/run-state.py phase collect-logs --status completed --run-id "$RUN_ID" || true
fi
log "Collection complete"
