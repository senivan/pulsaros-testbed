#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

log() { printf '[scenario] %s\n' "$*"; }
die() { printf '[scenario] ERROR: %s\n' "$*" >&2; exit 1; }

SCENARIO="${1:-}"
[[ -n "$SCENARIO" ]] || die "usage: $0 SCENARIO"
mkdir -p junit pcaps logs artifacts
record_state() {
  if [[ -f artifacts/topology.json || -f artifacts/run-state.json ]]; then
    ./scripts/run-state.py phase "scenario-${SCENARIO}" --status "$1" || true
  fi
}
finish_state() {
  local rc=$?
  if (( rc == 0 )); then
    record_state completed
  else
    record_state failed
  fi
  exit "$rc"
}
trap finish_state EXIT
record_state started

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
    [[ "${DATAPLANE:-linux-vxlan}" == "pulsaros-dpdk" ]] || \
      die "dpdk-smoke requires DATAPLANE=pulsaros-dpdk"
    run_pytest dpdk-smoke tests/test_hugepages.py tests/test_dpdk_vxlan.py
    ;;
  netstack-smoke|netstack-udp-smoke)
    [[ "${DATAPLANE:-linux-vxlan}" == "pulsaros-netstack" ]] || \
      die "$SCENARIO requires DATAPLANE=pulsaros-netstack"
    run_pytest netstack-health tests/test_kernel.py tests/test_hugepages.py tests/test_netstack.py
    run_pytest netstack-topology tests/test_topology_checks.py
    if [[ "$SCENARIO" == "netstack-udp-smoke" ]]; then
      run_pytest netstack-udp tests/test_netstack_udp.py
    fi
    PULSAROS_NETSTACK_SHUTDOWN=1 run_pytest \
      netstack-shutdown tests/test_netstack.py::test_netstack_service_stops_cleanly
    ;;
  topology-checks|vxlan-reference)
    case "${DATAPLANE:-linux-vxlan}" in
      linux-vxlan)
        run_pytest topology-checks tests/test_kernel.py tests/test_linux_vxlan.py tests/test_topology_checks.py
        ;;
      pulsaros-dpdk)
        run_pytest topology-checks tests/test_kernel.py tests/test_hugepages.py tests/test_dpdk_vxlan.py tests/test_topology_checks.py
        ;;
      pulsaros-netstack)
        run_pytest topology-checks tests/test_kernel.py tests/test_hugepages.py tests/test_netstack.py tests/test_topology_checks.py
        ;;
      *) die "unsupported DATAPLANE: ${DATAPLANE}" ;;
    esac
    ;;
  full)
    run_pytest kernel-smoke tests/test_kernel.py
    case "${DATAPLANE:-linux-vxlan}" in
      linux-vxlan)
        run_pytest dataplane-health tests/test_linux_vxlan.py
        run_pytest topology-checks tests/test_topology_checks.py
        run_pytest fault-injection tests/test_fault_injection.py
        ;;
      pulsaros-dpdk)
        run_pytest dataplane-health tests/test_hugepages.py tests/test_dpdk_vxlan.py
        run_pytest topology-checks tests/test_topology_checks.py
        run_pytest fault-injection tests/test_fault_injection.py
        ;;
      pulsaros-netstack)
        run_pytest dataplane-health tests/test_hugepages.py tests/test_netstack.py
        run_pytest topology-checks tests/test_topology_checks.py
        PULSAROS_NETSTACK_SHUTDOWN=1 run_pytest \
          netstack-shutdown tests/test_netstack.py::test_netstack_service_stops_cleanly
        ;;
      *) die "unsupported DATAPLANE: ${DATAPLANE}" ;;
    esac
    ;;
  *)
    die "unsupported scenario: $SCENARIO"
    ;;
esac
