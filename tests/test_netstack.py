import ipaddress
import os
import shlex

import pytest

from conftest import group_hosts, host_ip, ssh


def netstack_hosts(topology):
    hosts = group_hosts(topology, "netstacks")
    assert hosts, "topology has no netstack hosts"
    return hosts


def test_netstack_deployment_files_exist(topology, ssh_user, ssh_key):
    for host in netstack_hosts(topology):
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "test -x /usr/local/libexec/pulsaros/netstack/netstack-dp "
            "&& test -f /etc/pulsaros/netstack/netstack.conf "
            "&& test -f /etc/pulsaros/netstack/pci-map.json",
        )


def test_netstack_service_is_healthy(topology, ssh_user, ssh_key):
    for host in netstack_hosts(topology):
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "set -eu; sudo -n systemctl is-active pulsaros-netstack; "
            "sudo -n systemctl show pulsaros-netstack -p NRestarts --value; "
            "sudo -n journalctl -b -u pulsaros-netstack --no-pager",
        )
        lines = result.stdout.splitlines()
        assert lines[0] == "active"
        assert lines[1] == "0", f"{host} entered a service restart loop:\n{result.stdout}"
        assert "initialized one DPDK port; entering host input loop" in result.stdout


def test_netstack_dataplane_is_the_only_uio_bound_function(topology, ssh_user, ssh_key):
    resolved = topology["__resolved__"]
    for host in netstack_hosts(topology):
        expected_mac = next(
            nic["mac"] for nic in resolved["hosts"][host]["nics"]
            if not nic.get("management")
        )
        command = (
            "sudo -n python3 - <<'PY'\n"
            "import json\n"
            "from pathlib import Path\n"
            "mapping = json.loads(Path('/etc/pulsaros/netstack/pci-map.json').read_text())\n"
            "port = mapping['port']\n"
            f"assert port['mac'].lower() == {expected_mac.lower()!r}\n"
            "device = Path('/sys/bus/pci/devices') / port['pci']\n"
            "assert device.exists()\n"
            "assert (device / 'vendor').read_text().strip().lower() == '0x1af4'\n"
            "assert (device / 'driver').resolve().name == 'uio_pci_generic'\n"
            "bound = {path.name for path in Path('/sys/bus/pci/drivers/uio_pci_generic').glob('*:*')}\n"
            "assert bound == {port['pci']}, (bound, port['pci'])\n"
            "assert not any((path / 'address').read_text().strip().lower() == port['mac'].lower() "
            "for path in Path('/sys/class/net').iterdir())\n"
            "PY"
        )
        ssh(topology, ssh_user, ssh_key, host, command)


def test_netstack_management_nic_remains_kernel_owned(topology, ssh_user, ssh_key):
    resolved = topology["__resolved__"]
    for host in netstack_hosts(topology):
        management_mac = next(
            nic["mac"].lower()
            for nic in resolved["hosts"][host]["nics"]
            if nic.get("management")
        )
        management_ip = host_ip(topology, host)
        command = (
            "set -euo pipefail; "
            f"mac={shlex.quote(management_mac)}; "
            "iface=$(for path in /sys/class/net/*; do "
            "[ \"$(cat \"$path/address\" 2>/dev/null || true)\" = \"$mac\" ] "
            "&& basename \"$path\" && break; done); "
            "test -n \"$iface\"; "
            "test \"$(basename \"$(readlink -f \"/sys/class/net/$iface/device/driver\")\")\" != uio_pci_generic; "
            f"ip -4 -o address show dev \"$iface\" | grep -Fq {shlex.quote(management_ip)}"
        )
        ssh(topology, ssh_user, ssh_key, host, command)


def test_netstack_configuration_matches_topology(topology, ssh_user, ssh_key):
    resolved = topology["__resolved__"]
    for host in netstack_hosts(topology):
        expected_cidr = resolved["hosts"][host]["ansible_vars"]["netstack_ip"]
        expected_cidr = str(ipaddress.ip_interface(expected_cidr))
        command = (
            "sudo -n python3 - <<'PY'\n"
            "import json\n"
            "from pathlib import Path\n"
            "mapping = json.loads(Path('/etc/pulsaros/netstack/pci-map.json').read_text())\n"
            "config = {}\n"
            "for line in Path('/etc/pulsaros/netstack/netstack.conf').read_text().splitlines():\n"
            "    key, value = line.split('=', 1)\n"
            "    assert key not in config\n"
            "    config[key] = value\n"
            "assert config['pmd'] == 'physical'\n"
            "assert config['device'] == mapping['port']['pci']\n"
            f"assert config['ip'] == {expected_cidr!r}\n"
            "assert 'no_huge' not in config\n"
            "PY"
        )
        ssh(topology, ssh_user, ssh_key, host, command)


def test_netstack_service_stops_cleanly(topology, ssh_user, ssh_key):
    if os.environ.get("PULSAROS_NETSTACK_SHUTDOWN") != "1":
        pytest.skip("shutdown is verified after connectivity checks")
    for host in netstack_hosts(topology):
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "set -eu; sudo -n systemctl stop pulsaros-netstack; "
            "! sudo -n systemctl is-failed --quiet pulsaros-netstack; "
            "! sudo -n systemctl is-active --quiet pulsaros-netstack; "
            "test \"$(sudo -n systemctl show pulsaros-netstack -p ExecMainStatus --value)\" = 0",
        )
