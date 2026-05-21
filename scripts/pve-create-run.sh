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
  run_pve pvesh get "/cluster/sdn/zones/$zone" >/dev/null 2>&1 && die "SDN zone $zone already exists"
  while IFS=$'\t' read -r vnet _; do
    run_pve pvesh get "/cluster/sdn/vnets/$vnet" >/dev/null 2>&1 && die "SDN VNet $vnet already exists"
  done < <(jq -r '.networks[] | select(.vnet != "") | [.vnet, .inner_vlan] | @tsv' artifacts/topology.json)

  run_pve pvesh create /cluster/sdn/zones --type qinq --zone "$zone" --bridge "$sdn_bridge" --ipam "$ipam" --tag "$service_vlan" --mtu "$mtu"
  while IFS=$'\t' read -r vnet inner_vlan; do
    run_pve pvesh create /cluster/sdn/vnets --vnet "$vnet" --zone "$zone" --tag "$inner_vlan"
  done < <(jq -r '.networks[] | select(.vnet != "") | [.vnet, .inner_vlan] | @tsv' artifacts/topology.json)

  sdn_apply
  while IFS= read -r vnet; do
    wait_for_vnet "$vnet"
  done < <(jq -r '.networks[] | select(.vnet != "") | .vnet' artifacts/topology.json)
}

clone_vm() {
  local vmid="$1" name="$2"
  if run_pve qm status "$vmid" >/dev/null 2>&1; then
    die "target VMID $vmid already exists"
  fi
  log "Cloning $name as VMID $vmid"
  run_pve qm clone "$TEMPLATE_ID" "$vmid" --name "$name" --full 1 --storage "$STORAGE"
}

while IFS=$'\t' read -r vmid vm_name; do
  clone_vm "$vmid" "$vm_name"
done < <(jq -r '.hosts[] | [.vmid, .vm_name] | @tsv' artifacts/topology.json)

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
done < <(jq -r '.hosts[] | [.name, .vmid] | @tsv' artifacts/topology.json)

while IFS=$'\t' read -r vmid vm_name; do
  log "Starting $vm_name ($vmid)"
  run_pve qm start "$vmid"
done < <(jq -r '.hosts[] | [.vmid, .vm_name] | @tsv' artifacts/topology.json)

log "Created run $RUN_ID"
