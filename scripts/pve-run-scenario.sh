#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[scenario] %s\n' "$*"; }
die() { printf '[scenario] ERROR: %s\n' "$*" >&2; exit 1; }

SCENARIO="${1:-}"
[[ -n "$SCENARIO" ]] || die "usage: $0 SCENARIO"
mkdir -p junit pcaps logs artifacts

run_pytest() {
  local name="$1"
  shift
  log "Running pytest: $name"
  pytest "$@" --junitxml="junit/${name}.xml"
}

case "$SCENARIO" in
  kernel-smoke)
    run_pytest kernel-smoke tests/test_kernel.py
    ;;
  dpdk-smoke)
    run_pytest dpdk-smoke tests/test_hugepages.py tests/test_dpdk.py
    ;;
  topology-checks|linux-vxlan-reference)
    run_pytest topology-checks tests/test_kernel.py tests/test_topology_checks.py
    ;;
  full)
    run_pytest kernel-smoke tests/test_kernel.py
    run_pytest dpdk-smoke tests/test_hugepages.py tests/test_dpdk.py
    run_pytest topology-checks tests/test_topology_checks.py
    ;;
  *)
    die "unsupported scenario: $SCENARIO"
    ;;
esac
