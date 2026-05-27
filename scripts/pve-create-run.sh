#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[create] %s\n' "$*"; }
die() { printf '[create] ERROR: %s\n' "$*" >&2; exit 1; }
need_env() { [[ -n "${!1:-}" ]] || die "Required environment variable $1 is not set"; }
run_pve() {
  if (( EUID == 0 )); then
    "$@"
  else
    sudo -n "$@"
  fi
}

RUN_ID="${1:-}"
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || die "usage: $0 RUN_ID"
export RUN_ID
finish_create_state() {
  local rc=$?
  if (( rc != 0 )) && [[ -f artifacts/topology.json || -f artifacts/run-state.json ]]; then
    ./scripts/run-state.py phase create --status failed --message "pve-create-run.sh exited with $rc" --run-id "$RUN_ID" || true
  fi
  exit "$rc"
}
trap finish_create_state EXIT

need_env TEMPLATE_ID
need_env STORAGE
need_env MGMT_BRIDGE

NETWORK_MODE="${NETWORK_MODE:-qinq}"
case "$NETWORK_MODE" in
  qinq|bridge) ;;
  *) die "NETWORK_MODE must be qinq or bridge, got: $NETWORK_MODE" ;;
esac
export NETWORK_MODE

if [[ "$NETWORK_MODE" == "bridge" ]]; then
  need_env TEST_BRIDGE
else
  export SDN_BRIDGE="${SDN_BRIDGE:-${TEST_BRIDGE:-vmbr-test}}"
  export QINQ_SERVICE_VLAN_BASE="${QINQ_SERVICE_VLAN_BASE:-3000}"
  export QINQ_SERVICE_VLAN_COUNT="${QINQ_SERVICE_VLAN_COUNT:-500}"
  export QINQ_MTU="${QINQ_MTU:-1496}"
  export QINQ_IPAM="${QINQ_IPAM:-pve}"
fi

TOPOLOGY="${TOPOLOGY:-linux-vxlan-reference}"
TOPOLOGY_FILE="${TOPOLOGY_FILE:-topologies/${TOPOLOGY}.yml}"
[[ -f "$TOPOLOGY_FILE" ]] || die "topology file not found: $TOPOLOGY_FILE"

mkdir -p artifacts logs pcaps junit

log "Rendering topology $TOPOLOGY_FILE"
./scripts/render-topology.py render --topology-file "$TOPOLOGY_FILE"
./scripts/run-state.py init --phase rendered

sdn_apply() {
  log "Applying Proxmox SDN configuration"
  run_pve pvesh set /cluster/sdn
}

wait_for_vnet() {
  local vnet="$1" deadline
  deadline=$((SECONDS + 60))
  while (( SECONDS < deadline )); do
    if [[ -d "/sys/class/net/$vnet" ]]; then
      return 0
    fi
    sleep 2
  done
  die "VNet bridge $vnet did not appear after SDN apply"
}

create_sdn_qinq() {
  local zone service_vlan sdn_bridge mtu ipam
  zone=$(jq -r '.qinq.zone' artifacts/topology.json)
  service_vlan=$(jq -r '.qinq.service_vlan' artifacts/topology.json)
  sdn_bridge=$(jq -r '.qinq.sdn_bridge' artifacts/topology.json)
  mtu=$(jq -r '.qinq.mtu' artifacts/topology.json)
  ipam=$(jq -r '.qinq.ipam' artifacts/topology.json)

  log "Creating QinQ SDN zone $zone on $sdn_bridge with service VLAN $service_vlan"
  if run_pve pvesh get "/cluster/sdn/zones/$zone" >/dev/null 2>&1; then
    log "Reusing existing generated SDN zone $zone"
    ./scripts/run-state.py sdn zone "$zone" reused
  else
    run_pve pvesh create /cluster/sdn/zones --type qinq --zone "$zone" --bridge "$sdn_bridge" --ipam "$ipam" --tag "$service_vlan" --mtu "$mtu"
    ./scripts/run-state.py sdn zone "$zone" created
  fi
  while IFS=$'\t' read -r vnet inner_vlan; do
    if run_pve pvesh get "/cluster/sdn/vnets/$vnet" >/dev/null 2>&1; then
      log "Reusing existing generated SDN VNet $vnet"
      ./scripts/run-state.py sdn vnet "$vnet" reused
    else
      run_pve pvesh create /cluster/sdn/vnets --vnet "$vnet" --zone "$zone" --tag "$inner_vlan"
      ./scripts/run-state.py sdn vnet "$vnet" created
    fi
  done < <(jq -r '.networks[] | select(.vnet != "") | [.vnet, .inner_vlan] | @tsv' artifacts/topology.json)

  sdn_apply
  while IFS= read -r vnet; do
    wait_for_vnet "$vnet"
  done < <(jq -r '.networks[] | select(.vnet != "") | .vnet' artifacts/topology.json)
}

clone_vm() {
  local host="$1" vmid="$2" name="$3" actual_name
  if run_pve qm status "$vmid" >/dev/null 2>&1; then
    actual_name=$(run_pve qm config "$vmid" | awk -F': ' '/^name:/ {print $2}')
    if [[ "$actual_name" != "$name" ]]; then
      die "target VMID $vmid exists as $actual_name, expected $name"
    fi
    log "Reusing existing generated VM $name ($vmid)"
    ./scripts/run-state.py vm "$host" reused
    return 0
  fi
  log "Cloning $name as VMID $vmid"
  run_pve qm clone "$TEMPLATE_ID" "$vmid" --name "$name" --full 1 --storage "$STORAGE"
  ./scripts/run-state.py vm "$host" cloned
}

while IFS=$'\t' read -r host vmid vm_name; do
  clone_vm "$host" "$vmid" "$vm_name"
done < <(jq -r '.hosts[] | [.name, .vmid, .vm_name] | @tsv' artifacts/topology.json)

if [[ "$NETWORK_MODE" == "qinq" ]]; then
  create_sdn_qinq
fi

log "Attaching management and dataplane NICs"
while IFS=$'\t' read -r host vmid; do
  log "Configuring NICs for $host ($vmid)"
  run_pve qm set "$vmid" --serial0 socket
  while IFS=$'\t' read -r idx mac bridge; do
    run_pve qm set "$vmid" "--net${idx}" "virtio=$mac,bridge=$bridge,firewall=0"
  done < <(jq -r --arg host "$host" '.hosts[$host].nics | to_entries[] | [.key, .value.mac, .value.bridge] | @tsv' artifacts/topology.json)
  ./scripts/run-state.py vm "$host" configured
done < <(jq -r '.hosts[] | [.name, .vmid] | @tsv' artifacts/topology.json)

while IFS=$'\t' read -r host vmid vm_name; do
  if run_pve qm status "$vmid" | grep -q "status: running"; then
    log "$vm_name ($vmid) already running"
    ./scripts/run-state.py vm "$host" running
  else
    log "Starting $vm_name ($vmid)"
    run_pve qm start "$vmid"
    ./scripts/run-state.py vm "$host" started
  fi
done < <(jq -r '.hosts[] | [.name, .vmid, .vm_name] | @tsv' artifacts/topology.json)

./scripts/run-state.py phase created
trap - EXIT
log "Created run $RUN_ID"
