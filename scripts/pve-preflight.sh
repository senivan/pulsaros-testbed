#!/usr/bin/env bash
set -euo pipefail

log() { printf '[preflight] %s\n' "$*"; }
die() { printf '[preflight] ERROR: %s\n' "$*" >&2; exit 1; }
need_env() { [[ -n "${!1:-}" ]] || die "Required environment variable $1 is not set"; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

need_env TEMPLATE_ID
need_env STORAGE
need_env MGMT_BRIDGE
need_env TEST_BRIDGE

log "Checking required commands"
for cmd in qm pvesh pvecm pvesm ansible-playbook pytest ssh scp jq df; do
  need_cmd "$cmd"
done

log "Checking this looks like a Proxmox host"
[[ -d /etc/pve ]] || die "/etc/pve not found"
pvecm status >/dev/null 2>&1 || die "pvecm status failed"

log "Checking qm access"
qm list >/dev/null 2>&1 || die "runner cannot call qm list"

log "Checking template VM $TEMPLATE_ID"
qm status "$TEMPLATE_ID" >/dev/null 2>&1 || die "template VM $TEMPLATE_ID does not exist or is inaccessible"
if ! qm config "$TEMPLATE_ID" | grep -q '^template: 1'; then
  die "VM $TEMPLATE_ID exists but is not marked as a template"
fi

log "Checking storage $STORAGE"
pvesm status --storage "$STORAGE" >/dev/null 2>&1 || die "storage $STORAGE is not available"

log "Checking bridges $MGMT_BRIDGE and $TEST_BRIDGE"
[[ -d "/sys/class/net/$MGMT_BRIDGE" ]] || die "management bridge $MGMT_BRIDGE not found"
[[ -d "/sys/class/net/$TEST_BRIDGE" ]] || die "test bridge $TEST_BRIDGE not found"

log "Checking free disk space on current filesystem"
available_kb=$(df -Pk . | awk 'NR == 2 {print $4}')
min_kb=$((20 * 1024 * 1024))
if (( available_kb < min_kb )); then
  die "less than 20 GiB free on current filesystem"
fi

log "Preflight passed"
