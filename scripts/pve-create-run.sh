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
need_env TEST_BRIDGE

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

log "Writing topology to artifacts/topology.env"
cat > artifacts/topology.env <<EOF
RUN_ID=$RUN_ID
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

clone_vm "$CLIENT_A" "$CLIENT_A_NAME"
clone_vm "$VTEP_A" "$VTEP_A_NAME"
clone_vm "$VTEP_B" "$VTEP_B_NAME"
clone_vm "$CLIENT_B" "$CLIENT_B_NAME"

log "Attaching management and dataplane NICs"
run_pve qm set "$CLIENT_A" --net0 "virtio=$CLIENT_A_MGMT_MAC,bridge=$MGMT_BRIDGE" --net1 "virtio=$CLIENT_A_LEFT_MAC,bridge=$TEST_BRIDGE,tag=$LEFT_VLAN"
run_pve qm set "$VTEP_A" --net0 "virtio=$VTEP_A_MGMT_MAC,bridge=$MGMT_BRIDGE" --net1 "virtio=$VTEP_A_LEFT_MAC,bridge=$TEST_BRIDGE,tag=$LEFT_VLAN" --net2 "virtio=$VTEP_A_UNDERLAY_MAC,bridge=$TEST_BRIDGE,tag=$UNDERLAY_VLAN"
run_pve qm set "$VTEP_B" --net0 "virtio=$VTEP_B_MGMT_MAC,bridge=$MGMT_BRIDGE" --net1 "virtio=$VTEP_B_UNDERLAY_MAC,bridge=$TEST_BRIDGE,tag=$UNDERLAY_VLAN" --net2 "virtio=$VTEP_B_RIGHT_MAC,bridge=$TEST_BRIDGE,tag=$RIGHT_VLAN"
run_pve qm set "$CLIENT_B" --net0 "virtio=$CLIENT_B_MGMT_MAC,bridge=$MGMT_BRIDGE" --net1 "virtio=$CLIENT_B_RIGHT_MAC,bridge=$TEST_BRIDGE,tag=$RIGHT_VLAN"

for vmid in "$CLIENT_A" "$VTEP_A" "$VTEP_B" "$CLIENT_B"; do
  log "Starting VMID $vmid"
  run_pve qm start "$vmid"
done

log "Created run $RUN_ID"
