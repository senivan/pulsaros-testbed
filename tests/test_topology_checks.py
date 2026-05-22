import pathlib
import re
import shlex
import subprocess
import time

import pytest

from conftest import PCAPS, host_ip, host_nic, iface_by_mac, resolved_topology, scp_from, ssh


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


def test_topology_checks(topology, ssh_user, ssh_key):
    checks = resolved_topology(topology).get("checks", [])
    if not checks:
        pytest.skip("topology declares no checks")

    for check in checks:
        if check["type"] == "ping":
            _run_ping_check(topology, ssh_user, ssh_key, check)
        elif check["type"] == "packet_capture":
            _run_packet_capture_check(topology, ssh_user, ssh_key, check)
        else:
            pytest.fail(f"unsupported topology check type: {check['type']}")
