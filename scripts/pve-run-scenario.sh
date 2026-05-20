#!/usr/bin/env bash
set -euo pipefail

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
  linux-vxlan-reference)
    run_pytest linux-vxlan-reference tests/test_kernel.py tests/test_linux_vxlan.py
    ;;
  full)
    run_pytest kernel-smoke tests/test_kernel.py
    run_pytest dpdk-smoke tests/test_hugepages.py tests/test_dpdk.py
    run_pytest linux-vxlan-reference tests/test_linux_vxlan.py
    ;;
  *)
    die "unsupported scenario: $SCENARIO"
    ;;
esac
