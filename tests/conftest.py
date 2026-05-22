import os
import json
import pathlib
import shlex
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "artifacts" / "topology.env"
TOPOLOGY_JSON = ROOT / "artifacts" / "topology.json"
PCAPS = ROOT / "pcaps"


HOSTS = {
    "client-a": "CLIENT_A_IP",
    "client-b": "CLIENT_B_IP",
    "vtep-a": "VTEP_A_IP",
    "vtep-b": "VTEP_B_IP",
}


def _load_env_file(path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def _load_json_file(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def topology():
    data = _load_env_file(TOPOLOGY)
    resolved = _load_json_file(TOPOLOGY_JSON)
    if resolved:
        data["__resolved__"] = resolved
        data["__hosts__"] = resolved.get("hosts", {})
        missing = [
            host
            for host, values in data["__hosts__"].items()
            if not values.get("management_ip")
        ]
        if missing:
            pytest.fail(f"missing management IPs: {', '.join(missing)}")
        return data
    missing = [name for name in HOSTS.values() if not data.get(name)]
    if missing:
        pytest.fail(f"missing topology values: {', '.join(missing)}")
    return data


def all_hosts(topology):
    return tuple(topology.get("__hosts__", HOSTS).keys())


def resolved_topology(topology):
    return topology.get("__resolved__", {})


def group_hosts(topology, group):
    hosts = topology.get("__hosts__")
    if not hosts:
        if group == "vteps":
            return ("vtep-a", "vtep-b")
        if group == "clients":
            return ("client-a", "client-b")
        return all_hosts(topology)
    return tuple(name for name, values in hosts.items() if group in values.get("groups", []))


def host_ip(topology, host):
    if "__hosts__" in topology:
        return topology["__hosts__"][host]["management_ip"]
    return topology[HOSTS[host]]


def host_nic(topology, host, nic_name):
    for nic in topology["__hosts__"][host]["nics"]:
        if nic["name"] == nic_name:
            return nic
    pytest.fail(f"host {host} has no nic {nic_name}")


@pytest.fixture(scope="session")
def ssh_user():
    return os.environ.get("ANSIBLE_USER", "pulsar")


@pytest.fixture(scope="session")
def ssh_key():
    return pathlib.Path(
        os.environ.get("ANSIBLE_SSH_PRIVATE_KEY_FILE", "~/.ssh/pulsaros-testbed")
    ).expanduser()


def ssh(topology, ssh_user, ssh_key, host, command, timeout=60, check=True):
    ip = host_ip(topology, host)
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
        f"{ssh_user}@{ip}",
        command,
    ]
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode != 0:
        pytest.fail(
            f"ssh command failed on {host}: {shlex.quote(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def scp_from(topology, ssh_user, ssh_key, host, remote_path, local_path, timeout=60):
    ip = host_ip(topology, host)
    local_path = pathlib.Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "scp",
        "-i",
        str(ssh_key),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        f"{ssh_user}@{ip}:{remote_path}",
        str(local_path),
    ]
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)


def iface_by_mac(topology, ssh_user, ssh_key, host, mac):
    command = (
        "set -eu; "
        f"mac={shlex.quote(mac.lower())}; "
        "for dev in /sys/class/net/*; do "
        '[ "$(cat "$dev/address")" = "$mac" ] && basename "$dev" && exit 0; '
        "done; exit 1"
    )
    return ssh(topology, ssh_user, ssh_key, host, command).stdout.strip()
