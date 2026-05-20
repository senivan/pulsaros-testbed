#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[preflight] %s\n' "$*"; }
die() { printf '[preflight] ERROR: %s\n' "$*" >&2; exit 1; }
need_env() { [[ -n "${!1:-}" ]] || die "Required environment variable $1 is not set"; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }
run_pve() {
  if (( EUID == 0 )); then
    "$@"
  else
    sudo -n "$@"
  fi
}

need_env TEMPLATE_ID
need_env STORAGE
need_env MGMT_BRIDGE
need_env TEST_BRIDGE

log "Checking required commands"
for cmd in sudo qm pvesh pveversion pvesm ansible-playbook pytest ssh scp jq df; do
  need_cmd "$cmd"
done

log "Checking this looks like a Proxmox host"
[[ -d /etc/pve ]] || die "/etc/pve not found"
pveversion >/dev/null 2>&1 || run_pve pveversion >/dev/null 2>&1 || die "pveversion failed"
if command -v pvecm >/dev/null 2>&1; then
  pvecm status >/dev/null 2>&1 || run_pve pvecm status >/dev/null 2>&1 || log "pvecm status failed; continuing because standalone Proxmox hosts are supported"
fi

log "Checking qm access"
run_pve qm list >/dev/null 2>&1 || die "runner cannot call qm list through sudo -n"

log "Checking template VM $TEMPLATE_ID"
run_pve qm status "$TEMPLATE_ID" >/dev/null 2>&1 || die "template VM $TEMPLATE_ID does not exist or is inaccessible"
if ! run_pve qm config "$TEMPLATE_ID" | grep -q '^template: 1'; then
  die "VM $TEMPLATE_ID exists but is not marked as a template"
fi

log "Checking storage $STORAGE"
run_pve pvesm status --storage "$STORAGE" >/dev/null 2>&1 || die "storage $STORAGE is not available"

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
