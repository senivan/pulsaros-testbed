#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[inventory] %s\n' "$*"; }
die() { printf '[inventory] ERROR: %s\n' "$*" >&2; exit 1; }

RUN_ID="${1:-}"
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || die "usage: $0 RUN_ID"
[[ -f artifacts/topology.env ]] || die "artifacts/topology.env not found"
[[ -f artifacts/topology.json ]] || die "artifacts/topology.json not found"
REQUESTED_RUN_ID="$RUN_ID"
# shellcheck disable=SC1091
source artifacts/topology.env
[[ "${RUN_ID:-}" == "$REQUESTED_RUN_ID" ]] || die "topology RUN_ID does not match requested RUN_ID"

./scripts/render-topology.py ansible
log "Generated ansible/inventory.generated.ini and ansible/site.generated.yml"
