#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[cleanup-stale] %s\n' "$*"; }
die() { printf '[cleanup-stale] ERROR: %s\n' "$*" >&2; exit 1; }
run_pve() {
  if (( EUID == 0 )); then
    "$@"
  else
    sudo -n "$@"
  fi
}

OLDER_THAN_HOURS=""
YES=0

while (($#)); do
  case "$1" in
    --older-than-hours)
      OLDER_THAN_HOURS="${2:-}"
      shift 2
      ;;
    --yes)
      YES=1
      shift
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ "$OLDER_THAN_HOURS" =~ ^[0-9]+$ ]] || die "usage: $0 --older-than-hours HOURS [--yes]"

now=$(date +%s)
max_age=$((OLDER_THAN_HOURS * 3600))
found=0

while read -r vmid name; do
  [[ -n "$vmid" && -n "$name" ]] || continue
  if [[ ! "$name" =~ ^pulsar-[0-9]+-(client-a|vtep-a|vtep-b|client-b)$ ]]; then
    continue
  fi
  conf="/etc/pve/qemu-server/${vmid}.conf"
  [[ -e "$conf" ]] || continue
  mtime=$(stat -c %Y "$conf" 2>/dev/null || stat -f %m "$conf")
  age=$((now - mtime))
  if (( age < max_age )); then
    continue
  fi
  found=1
  if (( YES == 1 )); then
    log "Deleting stale VM $name ($vmid)"
    run_pve qm stop "$vmid" --skiplock 1 || true
    run_pve qm destroy "$vmid" --purge 1 || true
  else
    log "Would delete stale VM $name ($vmid)"
  fi
done < <(run_pve qm list | awk 'NR > 1 {print $1, $2}')

if (( found == 0 )); then
  log "No stale generated VMs found"
elif (( YES == 0 )); then
  log "Dry run only. Re-run with --yes to delete."
fi
