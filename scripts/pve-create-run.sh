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
need_env TEMPLATE_ID
need_env STORAGE
need_env MGMT_BRIDGE

NETWORK_MODE="${NETWORK_MODE:-qinq}"
case "$NETWORK_MODE" in
  qinq|bridge) ;;
  *) die "NETWORK_MODE must be qinq or bridge, got: $NETWORK_MODE" ;;
esac

if [[ "$NETWORK_MODE" == "bridge" ]]; then
  need_env TEST_BRIDGE
else
  SDN_BRIDGE="${SDN_BRIDGE:-${TEST_BRIDGE:-vmbr-test}}"
  QINQ_SERVICE_VLAN_BASE="${QINQ_SERVICE_VLAN_BASE:-3000}"
  QINQ_SERVICE_VLAN_COUNT="${QINQ_SERVICE_VLAN_COUNT:-500}"
  QINQ_MTU="${QINQ_MTU:-1496}"
  QINQ_IPAM="${QINQ_IPAM:-pve}"
  [[ "$QINQ_SERVICE_VLAN_BASE" =~ ^[0-9]+$ ]] || die "QINQ_SERVICE_VLAN_BASE must be numeric"
  [[ "$QINQ_SERVICE_VLAN_COUNT" =~ ^[0-9]+$ ]] || die "QINQ_SERVICE_VLAN_COUNT must be numeric"
  [[ "$QINQ_MTU" =~ ^[0-9]+$ ]] || die "QINQ_MTU must be numeric"
  (( QINQ_SERVICE_VLAN_COUNT > 0 )) || die "QINQ_SERVICE_VLAN_COUNT must be greater than zero"
fi

mkdir -p artifacts logs pcaps junit

BASE=$(( 200000 + RUN_ID % 50000 ))
CLIENT_A=$((BASE + 1))
VTEP_A=$((BASE + 2))
VTEP_B=$((BASE + 3))
CLIENT_B=$((BASE + 4))

VLAN_BASE=$(( 3000 + RUN_ID % 500 ))
LEFT_VLAN=$((VLAN_BASE + 1))
UNDERLAY_VLAN=$((VLAN_BASE + 2))
RIGHT_VLAN=$((VLAN_BASE + 3))
LEFT_INNER_VLAN=101
UNDERLAY_INNER_VLAN=102
RIGHT_INNER_VLAN=103

if [[ "$NETWORK_MODE" == "qinq" ]]; then
  RUN_SUFFIX=$(printf '%06d' $((RUN_ID % 1000000)))
  QINQ_ZONE="pq${RUN_SUFFIX}"
  LEFT_VNET="pl${RUN_SUFFIX}"
  UNDERLAY_VNET="pu${RUN_SUFFIX}"
  RIGHT_VNET="pr${RUN_SUFFIX}"
  QINQ_SERVICE_VLAN=$((QINQ_SERVICE_VLAN_BASE + RUN_ID % QINQ_SERVICE_VLAN_COUNT))
  LEFT_VLAN="$LEFT_INNER_VLAN"
  UNDERLAY_VLAN="$UNDERLAY_INNER_VLAN"
  RIGHT_VLAN="$RIGHT_INNER_VLAN"
fi

mac_for() {
  local offset="$1"
  printf '52:54:%02x:%02x:%02x:%02x' \
    $(( (RUN_ID >> 16) & 255 )) \
    $(( (RUN_ID >> 8) & 255 )) \
    $(( RUN_ID & 255 )) \
    "$offset"
}

CLIENT_A_MGMT_MAC=$(mac_for 1)
CLIENT_A_LEFT_MAC=$(mac_for 11)
VTEP_A_MGMT_MAC=$(mac_for 2)
VTEP_A_LEFT_MAC=$(mac_for 12)
VTEP_A_UNDERLAY_MAC=$(mac_for 22)
VTEP_B_MGMT_MAC=$(mac_for 3)
VTEP_B_UNDERLAY_MAC=$(mac_for 23)
VTEP_B_RIGHT_MAC=$(mac_for 33)
CLIENT_B_MGMT_MAC=$(mac_for 4)
CLIENT_B_RIGHT_MAC=$(mac_for 14)

CLIENT_A_NAME="pulsar-${RUN_ID}-client-a"
VTEP_A_NAME="pulsar-${RUN_ID}-vtep-a"
VTEP_B_NAME="pulsar-${RUN_ID}-vtep-b"
CLIENT_B_NAME="pulsar-${RUN_ID}-client-b"

clone_vm() {
  local vmid="$1" name="$2"
  if run_pve qm status "$vmid" >/dev/null 2>&1; then
    die "target VMID $vmid already exists"
  fi
  log "Cloning $name as VMID $vmid"
  run_pve qm clone "$TEMPLATE_ID" "$vmid" --name "$name" --full 1 --storage "$STORAGE"
}

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
  log "Creating QinQ SDN zone $QINQ_ZONE on $SDN_BRIDGE with service VLAN $QINQ_SERVICE_VLAN"
  run_pve pvesh get "/cluster/sdn/zones/$QINQ_ZONE" >/dev/null 2>&1 && die "SDN zone $QINQ_ZONE already exists"
  run_pve pvesh get "/cluster/sdn/vnets/$LEFT_VNET" >/dev/null 2>&1 && die "SDN VNet $LEFT_VNET already exists"
  run_pve pvesh get "/cluster/sdn/vnets/$UNDERLAY_VNET" >/dev/null 2>&1 && die "SDN VNet $UNDERLAY_VNET already exists"
  run_pve pvesh get "/cluster/sdn/vnets/$RIGHT_VNET" >/dev/null 2>&1 && die "SDN VNet $RIGHT_VNET already exists"
  run_pve pvesh create /cluster/sdn/zones --type qinq --zone "$QINQ_ZONE" --bridge "$SDN_BRIDGE" --ipam "$QINQ_IPAM" --tag "$QINQ_SERVICE_VLAN" --mtu "$QINQ_MTU"
  run_pve pvesh create /cluster/sdn/vnets --vnet "$LEFT_VNET" --zone "$QINQ_ZONE" --tag "$LEFT_INNER_VLAN"
  run_pve pvesh create /cluster/sdn/vnets --vnet "$UNDERLAY_VNET" --zone "$QINQ_ZONE" --tag "$UNDERLAY_INNER_VLAN"
  run_pve pvesh create /cluster/sdn/vnets --vnet "$RIGHT_VNET" --zone "$QINQ_ZONE" --tag "$RIGHT_INNER_VLAN"
  sdn_apply
  wait_for_vnet "$LEFT_VNET"
  wait_for_vnet "$UNDERLAY_VNET"
  wait_for_vnet "$RIGHT_VNET"
}

log "Writing topology to artifacts/topology.env"
cat > artifacts/topology.env <<EOF
RUN_ID=$RUN_ID
NETWORK_MODE=$NETWORK_MODE
BASE=$BASE
CLIENT_A=$CLIENT_A
VTEP_A=$VTEP_A
VTEP_B=$VTEP_B
CLIENT_B=$CLIENT_B
CLIENT_A_NAME=$CLIENT_A_NAME
VTEP_A_NAME=$VTEP_A_NAME
VTEP_B_NAME=$VTEP_B_NAME
CLIENT_B_NAME=$CLIENT_B_NAME
LEFT_VLAN=$LEFT_VLAN
UNDERLAY_VLAN=$UNDERLAY_VLAN
RIGHT_VLAN=$RIGHT_VLAN
LEFT_INNER_VLAN=$LEFT_INNER_VLAN
UNDERLAY_INNER_VLAN=$UNDERLAY_INNER_VLAN
RIGHT_INNER_VLAN=$RIGHT_INNER_VLAN
CLIENT_A_MGMT_MAC=$CLIENT_A_MGMT_MAC
CLIENT_A_LEFT_MAC=$CLIENT_A_LEFT_MAC
VTEP_A_MGMT_MAC=$VTEP_A_MGMT_MAC
VTEP_A_LEFT_MAC=$VTEP_A_LEFT_MAC
VTEP_A_UNDERLAY_MAC=$VTEP_A_UNDERLAY_MAC
VTEP_B_MGMT_MAC=$VTEP_B_MGMT_MAC
VTEP_B_UNDERLAY_MAC=$VTEP_B_UNDERLAY_MAC
VTEP_B_RIGHT_MAC=$VTEP_B_RIGHT_MAC
CLIENT_B_MGMT_MAC=$CLIENT_B_MGMT_MAC
CLIENT_B_RIGHT_MAC=$CLIENT_B_RIGHT_MAC
EOF

if [[ "$NETWORK_MODE" == "qinq" ]]; then
  cat >> artifacts/topology.env <<EOF
SDN_BRIDGE=$SDN_BRIDGE
QINQ_ZONE=$QINQ_ZONE
QINQ_SERVICE_VLAN=$QINQ_SERVICE_VLAN
QINQ_MTU=$QINQ_MTU
LEFT_VNET=$LEFT_VNET
UNDERLAY_VNET=$UNDERLAY_VNET
RIGHT_VNET=$RIGHT_VNET
EOF
fi

clone_vm "$CLIENT_A" "$CLIENT_A_NAME"
clone_vm "$VTEP_A" "$VTEP_A_NAME"
clone_vm "$VTEP_B" "$VTEP_B_NAME"
clone_vm "$CLIENT_B" "$CLIENT_B_NAME"

if [[ "$NETWORK_MODE" == "qinq" ]]; then
  create_sdn_qinq
  LEFT_NET="$LEFT_VNET"
  UNDERLAY_NET="$UNDERLAY_VNET"
  RIGHT_NET="$RIGHT_VNET"
else
  LEFT_NET="$TEST_BRIDGE,tag=$LEFT_VLAN"
  UNDERLAY_NET="$TEST_BRIDGE,tag=$UNDERLAY_VLAN"
  RIGHT_NET="$TEST_BRIDGE,tag=$RIGHT_VLAN"
fi

log "Attaching management and dataplane NICs"
run_pve qm set "$CLIENT_A" --serial0 socket --net0 "virtio=$CLIENT_A_MGMT_MAC,bridge=$MGMT_BRIDGE,firewall=0" --net1 "virtio=$CLIENT_A_LEFT_MAC,bridge=$LEFT_NET,firewall=0"
run_pve qm set "$VTEP_A" --serial0 socket --net0 "virtio=$VTEP_A_MGMT_MAC,bridge=$MGMT_BRIDGE,firewall=0" --net1 "virtio=$VTEP_A_LEFT_MAC,bridge=$LEFT_NET,firewall=0" --net2 "virtio=$VTEP_A_UNDERLAY_MAC,bridge=$UNDERLAY_NET,firewall=0"
run_pve qm set "$VTEP_B" --serial0 socket --net0 "virtio=$VTEP_B_MGMT_MAC,bridge=$MGMT_BRIDGE,firewall=0" --net1 "virtio=$VTEP_B_UNDERLAY_MAC,bridge=$UNDERLAY_NET,firewall=0" --net2 "virtio=$VTEP_B_RIGHT_MAC,bridge=$RIGHT_NET,firewall=0"
run_pve qm set "$CLIENT_B" --serial0 socket --net0 "virtio=$CLIENT_B_MGMT_MAC,bridge=$MGMT_BRIDGE,firewall=0" --net1 "virtio=$CLIENT_B_RIGHT_MAC,bridge=$RIGHT_NET,firewall=0"

for vmid in "$CLIENT_A" "$VTEP_A" "$VTEP_B" "$CLIENT_B"; do
  log "Starting VMID $vmid"
  run_pve qm start "$vmid"
done

log "Created run $RUN_ID"
