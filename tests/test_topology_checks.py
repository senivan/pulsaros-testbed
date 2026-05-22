import json
import pathlib
import re
import shlex
import subprocess
import time

import pytest

from conftest import PCAPS, TOPOLOGY_JSON, host_ip, host_nic, iface_by_mac, scp_from, ssh


def _load_checks_for_collection():
    if not TOPOLOGY_JSON.exists():
        return []
    data = json.loads(TOPOLOGY_JSON.read_text(encoding="utf-8"))
    return data.get("checks", [])


def pytest_generate_tests(metafunc):
    if "topology_check" not in metafunc.fixturenames:
        return
    checks = _load_checks_for_collection()
    if checks:
        metafunc.parametrize(
            "topology_check",
            checks,
            ids=[check.get("name", f"check-{index}") for index, check in enumerate(checks)],
        )
    else:
        metafunc.parametrize("topology_check", [None], ids=["no-topology-checks"])


def _check_value(check, name, default):
    return int(check.get(name, default))


def _run_ping_check(topology, ssh_user, ssh_key, check):
    count = _check_value(check, "count", 3)
    wait = _check_value(check, "wait", 2)
    command = f"ping -c {count} -W {wait} {shlex.quote(str(check['destination']))}"
    ssh(
        topology,
        ssh_user,
        ssh_key,
        check["source"],
        command,
        timeout=_check_value(check, "timeout", 60),
    )


def _pcap_name(check_name, capture):
    raw = f"{check_name}-{capture['host']}-{capture['nic']}.pcap"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", raw)


def _start_capture(topology, ssh_user, ssh_key, check_name, capture):
    nic = host_nic(topology, capture["host"], capture["nic"])
    capture_if = iface_by_mac(topology, ssh_user, ssh_key, capture["host"], nic["mac"])
    remote_pcap = f"/tmp/pulsaros-testbed/{_pcap_name(check_name, capture)}"
    local_pcap = PCAPS / _pcap_name(check_name, capture)

    ssh(
        topology,
        ssh_user,
        ssh_key,
        capture["host"],
        f"sudo -n mkdir -p /tmp/pulsaros-testbed && sudo -n rm -f {remote_pcap}",
    )
    args = [
        "ssh",
        "-i",
        str(ssh_key),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        f"{ssh_user}@{host_ip(topology, capture['host'])}",
        "sudo -n sh -c "
        + shlex.quote(
            f"timeout 20 tcpdump -U -i {capture_if} -w {remote_pcap} {capture['filter']}"
        ),
    ]
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {
        "host": capture["host"],
        "remote_pcap": remote_pcap,
        "local_pcap": local_pcap,
        "process": process,
    }


def _stop_capture(capture):
    process = capture["process"]
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _collect_capture(topology, ssh_user, ssh_key, capture):
    ssh(
        topology,
        ssh_user,
        ssh_key,
        capture["host"],
        f"sudo -n chmod 0644 {capture['remote_pcap']} && test -s {capture['remote_pcap']}",
        timeout=30,
    )
    result = scp_from(
        topology,
        ssh_user,
        ssh_key,
        capture["host"],
        capture["remote_pcap"],
        capture["local_pcap"],
    )
    assert result.returncode == 0, result.stderr
    assert pathlib.Path(capture["local_pcap"]).stat().st_size > 0


def _run_packet_capture_check(topology, ssh_user, ssh_key, check):
    captures = []
    try:
        captures = [
            _start_capture(topology, ssh_user, ssh_key, check["name"], capture)
            for capture in check["captures"]
        ]
        time.sleep(_check_value(check, "settle", 2))
        _run_ping_check(topology, ssh_user, ssh_key, check["trigger"])
        time.sleep(_check_value(check, "post_trigger_wait", 3))
    finally:
        for capture in captures:
            _stop_capture(capture)

    for capture in captures:
        _collect_capture(topology, ssh_user, ssh_key, capture)


def _lua_string(value):
    return json.dumps(str(value))


def _run_pktgen_dpdk_check(topology, ssh_user, ssh_key, check):
    nic = host_nic(topology, check["source"], check["nic"])
    source_if = iface_by_mac(topology, ssh_user, ssh_key, check["source"], nic["mac"])
    duration_ms = _check_value(check, "duration", 5) * 1000
    timeout = _check_value(check, "timeout", 30)
    packet_count = _check_value(check, "count", 0)
    packet_size = _check_value(check, "packet_size", 128)
    rate_percent = _check_value(check, "rate_percent", 1)
    sport = _check_value(check, "source_port", 1234)
    dport = _check_value(check, "destination_port", 5678)
    protocol = check.get("protocol", "udp")
    remote_dir = "/tmp/pulsaros-testbed"
    script_path = f"{remote_dir}/{check['name']}.lua"
    log_path = f"{remote_dir}/{check['name']}.log"
    lua = f"""
package.path = package.path ..";/usr/local/share/pulsaros-pktgen/?.lua;?.lua;test/?.lua;app/?.lua;"
require "Pktgen"
pktgen.screen("off")
pktgen.set("all", "count", {packet_count})
pktgen.set("all", "rate", {rate_percent})
pktgen.set("all", "size", {packet_size})
pktgen.set("all", "sport", {sport})
pktgen.set("all", "dport", {dport})
pktgen.set_mac("all", "src", {_lua_string(nic["mac"])})
pktgen.set_mac("all", "dst", {_lua_string(check["destination_mac"])})
pktgen.set_ipaddr("all", "src", {_lua_string(check["source_ip"])})
pktgen.set_ipaddr("all", "dst", {_lua_string(check["destination_ip"])})
pktgen.set_proto("all", {_lua_string(protocol)})
pktgen.start("all")
pktgen.delay({duration_ms})
pktgen.stop("all")
prints("pktStats", pktgen.pktStats("all"))
pktgen.quit()
"""
    command = (
        "set -euo pipefail; "
        f"mkdir -p {shlex.quote(remote_dir)}; "
        "pktgen_bin=$(command -v pktgen-dpdk || command -v pktgen || true); "
        'if [ -z "$pktgen_bin" ]; then echo "pktgen-dpdk/pktgen not found" >&2; exit 127; fi; '
        f"cat > {shlex.quote(script_path)} <<'PULSAROS_PKTGEN_LUA'\n"
        f"{lua}"
        "PULSAROS_PKTGEN_LUA\n"
        f"timeout {timeout} \"$pktgen_bin\" -l 0-1 -n 2 --no-pci "
        f"--vdev=net_af_packet0,iface={shlex.quote(source_if)} -- "
        f"-T -P -m '[1].0' -f {shlex.quote(script_path)} "
        f"> {shlex.quote(log_path)} 2>&1; "
        f"cat {shlex.quote(log_path)}"
    )
    ssh(
        topology,
        ssh_user,
        ssh_key,
        check["source"],
        f"sudo -n bash -lc {shlex.quote(command)}",
        timeout=timeout + 10,
    )


def test_topology_check(topology, ssh_user, ssh_key, topology_check):
    if topology_check is None:
        pytest.skip("topology declares no checks")

    if topology_check["type"] == "ping":
        _run_ping_check(topology, ssh_user, ssh_key, topology_check)
    elif topology_check["type"] == "packet_capture":
        _run_packet_capture_check(topology, ssh_user, ssh_key, topology_check)
    elif topology_check["type"] == "pktgen_dpdk":
        _run_pktgen_dpdk_check(topology, ssh_user, ssh_key, topology_check)
    else:
        pytest.fail(f"unsupported topology check type: {topology_check['type']}")
