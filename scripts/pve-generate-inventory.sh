#!/usr/bin/env bash
set -euo pipefail

log() { printf '[inventory] %s\n' "$*"; }
die() { printf '[inventory] ERROR: %s\n' "$*" >&2; exit 1; }

RUN_ID="${1:-}"
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || die "usage: $0 RUN_ID"
[[ -f artifacts/topology.env ]] || die "artifacts/topology.env not found"
REQUESTED_RUN_ID="$RUN_ID"
# shellcheck disable=SC1091
source artifacts/topology.env
[[ "${RUN_ID:-}" == "$REQUESTED_RUN_ID" ]] || die "topology RUN_ID does not match requested RUN_ID"

for var in CLIENT_A_IP CLIENT_B_IP VTEP_A_IP VTEP_B_IP; do
  [[ -n "${!var:-}" ]] || die "$var missing from topology.env; run pve-wait-ssh.sh first"
done

ANSIBLE_USER_VALUE="${ANSIBLE_USER:-pulsar}"
ANSIBLE_KEY="${ANSIBLE_SSH_PRIVATE_KEY_FILE:-~/.ssh/pulsaros-testbed}"

mkdir -p ansible
cat > ansible/inventory.generated.ini <<EOF
[clients]
client-a ansible_host=$CLIENT_A_IP dataplane_mac=$CLIENT_A_LEFT_MAC dataplane_ip=10.10.0.1/24
client-b ansible_host=$CLIENT_B_IP dataplane_mac=$CLIENT_B_RIGHT_MAC dataplane_ip=10.10.0.2/24

[vteps]
vtep-a ansible_host=$VTEP_A_IP left_mac=$VTEP_A_LEFT_MAC underlay_mac=$VTEP_A_UNDERLAY_MAC underlay_ip=172.16.100.1/30 vxlan_remote=172.16.100.2
vtep-b ansible_host=$VTEP_B_IP underlay_mac=$VTEP_B_UNDERLAY_MAC right_mac=$VTEP_B_RIGHT_MAC underlay_ip=172.16.100.2/30 vxlan_remote=172.16.100.1

[all:vars]
ansible_user=$ANSIBLE_USER_VALUE
ansible_ssh_private_key_file=$ANSIBLE_KEY
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
EOF

log "Generated ansible/inventory.generated.ini"
