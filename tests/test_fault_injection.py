import json
import ipaddress
import pathlib
import shlex
import time

import pytest

from conftest import TOPOLOGY_JSON, host_nic, iface_by_mac, ssh


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
FAULT_RESULTS = ARTIFACTS / "fault-results.json"


def _load_faults_for_collection():
    if not TOPOLOGY_JSON.exists():
        return []
    data = json.loads(TOPOLOGY_JSON.read_text(encoding="utf-8"))
    return data.get("faults", [])


def pytest_generate_tests(metafunc):
    if "fault_definition" not in metafunc.fixturenames:
        return
    faults = _load_faults_for_collection()
    if faults:
        metafunc.parametrize(
            "fault_definition",
            faults,
            ids=[fault.get("name", f"fault-{index}") for index, fault in enumerate(faults)],
        )
    else:
        metafunc.parametrize("fault_definition", [None], ids=["no-faults"])


def _address(cidr):
    return str(ipaddress.ip_interface(str(cidr)).ip)


def _segment_members(segment):
    return {
        f"{member['host']}.{member['nic']}": member
        for member in segment.get("members", [])
    }


def _fault_pair(segment, fault):
    members = _segment_members(segment)
    pairs = fault.get("pairs") or []
    if len(pairs) != 1:
        pytest.fail(f"{fault['name']}: fault must define exactly one pair")
    pair = pairs[0]
    return members[pair["source"]], members[pair["destination"]]


def _append_fault_result(result):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(FAULT_RESULTS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"faults": []}
    data.setdefault("faults", []).append(result)
    FAULT_RESULTS.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _host_nic_network(topology, host, nic_name):
    return host_nic(topology, host, nic_name)["network"]


def _attached_vtep(topology, segment, member):
    member_network = _host_nic_network(topology, member["host"], member["nic"])
    for vtep in segment.get("vteps", []):
        for local_nic in vtep.get("local_nics", []):
            if local_nic.get("network") == member_network:
                return vtep
    pytest.fail(
        f"no VTEP local NIC found for {member['host']}.{member['nic']} "
        f"on network {member_network}"
    )


def _member_ifaces(topology, ssh_user, ssh_key, member):
    nic = host_nic(topology, member["host"], member["nic"])
    base = iface_by_mac(topology, ssh_user, ssh_key, member["host"], nic["mac"])
    if member.get("mode", "access") == "trunk":
        return base, f"{base}.{member['vlan']}"
    return base, base


def _sudo(topology, ssh_user, ssh_key, host, command, timeout=60, check=True):
    return ssh(
        topology,
        ssh_user,
        ssh_key,
        host,
        f"sudo -n bash -lc {shlex.quote(command)}",
        timeout=timeout,
        check=check,
    )


def _ping(topology, ssh_user, ssh_key, source_host, destination_ip, *, size=None, timeout=20):
    args = ["ping", "-c", "2", "-W", "2"]
    if size is not None:
        args.extend(["-M", "do", "-s", str(size)])
    args.append(shlex.quote(destination_ip))
    command = " ".join(args)
    return ssh(
        topology,
        ssh_user,
        ssh_key,
        source_host,
        command,
        timeout=timeout,
        check=False,
    )


def _assert_down(result, fault_name):
    if result.returncode == 255:
        pytest.fail(f"{fault_name}: ssh failed while checking expected outage:\n{result.stderr}")
    if result.returncode == 0:
        pytest.fail(f"{fault_name}: traffic stayed reachable during injected fault")


def _wait_reachable(topology, ssh_user, ssh_key, source_host, destination_ip, timeout):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _ping(topology, ssh_user, ssh_key, source_host, destination_ip, timeout=20)
        if last.returncode == 0:
            return last
        time.sleep(2)
    detail = ""
    if last is not None:
        detail = f"\nstdout:\n{last.stdout}\nstderr:\n{last.stderr}"
    pytest.fail(f"traffic did not recover from {source_host} to {destination_ip}{detail}")


def _inject_remove_fdb_peer(topology, ssh_user, ssh_key, segment, source, destination):
    source_vtep = _attached_vtep(topology, segment, source)
    vxlan = segment["vxlan"]
    _ = _attached_vtep(topology, segment, destination)
    peers = [
        vtep["underlay_address"]
        for vtep in segment.get("vteps", [])
        if vtep["host"] != source_vtep["host"]
    ]
    macs = ["00:00:00:00:00:00", destination["mac"]]
    delete = " ".join(
        (
            f"for attempt in 1 2 3 4 5; do "
            f"bridge fdb del {shlex.quote(mac)} dev {shlex.quote(vxlan)} "
            f"dst {shlex.quote(peer)} self 2>/dev/null || break; "
            f"done;"
        )
        for peer in peers
        for mac in macs
    )
    restore = " ".join(
        (
            f"bridge fdb append 00:00:00:00:00:00 dev {shlex.quote(vxlan)} "
            f"dst {shlex.quote(peer)} self || true;"
        )
        for peer in peers
    )
    _sudo(topology, ssh_user, ssh_key, source_vtep["host"], delete)
    return lambda: _sudo(topology, ssh_user, ssh_key, source_vtep["host"], restore, check=False)


def _inject_mtu_mismatch(topology, ssh_user, ssh_key, source):
    _, dataplane = _member_ifaces(topology, ssh_user, ssh_key, source)
    mtu_result = _sudo(
        topology,
        ssh_user,
        ssh_key,
        source["host"],
        f"cat /sys/class/net/{shlex.quote(dataplane)}/mtu",
    )
    original_mtu = mtu_result.stdout.strip()
    _sudo(
        topology,
        ssh_user,
        ssh_key,
        source["host"],
        f"ip link set dev {shlex.quote(dataplane)} mtu 1200",
    )
    return lambda: _sudo(
        topology,
        ssh_user,
        ssh_key,
        source["host"],
        f"ip link set dev {shlex.quote(dataplane)} mtu {shlex.quote(original_mtu)}",
        check=False,
    )


def _inject_vlan_mismatch(topology, ssh_user, ssh_key, fault, source):
    if source.get("mode", "access") != "trunk":
        pytest.fail(f"{fault['name']}: vlan_mismatch requires a trunk source member")
    base, correct = _member_ifaces(topology, ssh_user, ssh_key, source)
    current_vlan = int(source["vlan"])
    fault_vlan = int(fault.get("fault_vlan") or (current_vlan + 1 if current_vlan < 4094 else 1))
    if fault_vlan == current_vlan:
        pytest.fail(f"{fault['name']}: fault_vlan must differ from source VLAN {current_vlan}")
    wrong = f"{base}.{fault_vlan}"
    source_ip = source["ip"]
    command = (
        f"ip link add link {shlex.quote(base)} name {shlex.quote(wrong)} "
        f"type vlan id {fault_vlan} 2>/dev/null || true; "
        f"ip addr flush dev {shlex.quote(correct)}; "
        f"ip addr flush dev {shlex.quote(wrong)}; "
        f"ip addr replace {shlex.quote(source_ip)} dev {shlex.quote(wrong)}; "
        f"ip link set dev {shlex.quote(base)} up; "
        f"ip link set dev {shlex.quote(wrong)} up"
    )
    restore = (
        f"ip addr flush dev {shlex.quote(wrong)} 2>/dev/null || true; "
        f"ip addr replace {shlex.quote(source_ip)} dev {shlex.quote(correct)}; "
        f"ip link set dev {shlex.quote(correct)} up; "
        f"ip link del {shlex.quote(wrong)} 2>/dev/null || true"
    )
    _sudo(topology, ssh_user, ssh_key, source["host"], command)
    return lambda: _sudo(topology, ssh_user, ssh_key, source["host"], restore, check=False)


def _inject_bounce_vtep_underlay(topology, ssh_user, ssh_key, segment, source):
    source_vtep = _attached_vtep(topology, segment, source)
    nic = host_nic(topology, source_vtep["host"], source_vtep["underlay_nic"])
    underlay = iface_by_mac(topology, ssh_user, ssh_key, source_vtep["host"], nic["mac"])
    _sudo(
        topology,
        ssh_user,
        ssh_key,
        source_vtep["host"],
        f"ip link set dev {shlex.quote(underlay)} down",
    )
    return lambda: _sudo(
        topology,
        ssh_user,
        ssh_key,
        source_vtep["host"],
        f"ip link set dev {shlex.quote(underlay)} up",
        check=False,
    )


def test_fault_injection(fault_definition, request, ssh_user, ssh_key):
    if fault_definition is None:
        pytest.skip("topology declares no faults")

    topology = request.getfixturevalue("topology")
    resolved = topology["__resolved__"]
    segment = resolved["segments"][fault_definition["segment"]]
    source, destination = _fault_pair(segment, fault_definition)
    destination_ip = _address(destination["ip"])
    recover_timeout = int(fault_definition.get("recover_timeout", 45))
    result = {
        "name": fault_definition["name"],
        "type": fault_definition["type"],
        "segment": fault_definition["segment"],
        "source": f"{source['host']}.{source['nic']}",
        "destination": f"{destination['host']}.{destination['nic']}",
        "destination_ip": destination_ip,
        "restored": False,
        "recovered": False,
    }

    try:
        _wait_reachable(topology, ssh_user, ssh_key, source["host"], destination_ip, recover_timeout)
        restore = None
        fault_type = fault_definition["type"]
        try:
            if fault_type == "remove_fdb_peer":
                restore = _inject_remove_fdb_peer(
                    topology, ssh_user, ssh_key, segment, source, destination
                )
                down_result = _ping(topology, ssh_user, ssh_key, source["host"], destination_ip)
            elif fault_type == "mtu_mismatch":
                restore = _inject_mtu_mismatch(topology, ssh_user, ssh_key, source)
                down_result = _ping(
                    topology,
                    ssh_user,
                    ssh_key,
                    source["host"],
                    destination_ip,
                    size=int(fault_definition.get("packet_size", 1400)),
                )
            elif fault_type == "vlan_mismatch":
                restore = _inject_vlan_mismatch(
                    topology, ssh_user, ssh_key, fault_definition, source
                )
                down_result = _ping(topology, ssh_user, ssh_key, source["host"], destination_ip)
            elif fault_type == "bounce_vtep_underlay":
                restore = _inject_bounce_vtep_underlay(
                    topology, ssh_user, ssh_key, segment, source
                )
                time.sleep(int(fault_definition.get("settle", 2)))
                down_result = _ping(topology, ssh_user, ssh_key, source["host"], destination_ip)
            else:
                pytest.fail(f"unsupported fault type: {fault_type}")

            result["down_returncode"] = down_result.returncode
            _assert_down(down_result, fault_definition["name"])
        finally:
            if restore is not None:
                restore()
                result["restored"] = True

        _wait_reachable(topology, ssh_user, ssh_key, source["host"], destination_ip, recover_timeout)
        result["recovered"] = True
    finally:
        _append_fault_result(result)
