from conftest import group_hosts, ssh


def test_pulsaros_vxlan_service_is_running(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "sudo -n systemctl is-active --quiet pulsaros-vxlan",
        )


def test_linux_vxlan_dataplane_is_absent(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "ip -d -o link show type vxlan",
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert not result.stdout.strip(), f"Linux VXLAN link present on {host}: {result.stdout}"


def test_pulsaros_vxlan_uses_static_peers(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            (
                "sudo -n python3 - <<'PY'\n"
                "import json\n"
                "from pathlib import Path\n"
                "config = json.loads(Path('/etc/pulsaros/vxlan/config.json').read_text())\n"
                "assert config['vxlan']['controlplane'] == 'static'\n"
                "assert config['vxlan']['segments']\n"
                "assert all(segment['peers'] for segment in config['vxlan']['segments'])\n"
                "assert all(port['pmd'] == 'phys' and port.get('pci') for port in config['ports'])\n"
                "assert all('iface' not in port for port in config['ports'])\n"
                "PY"
            ),
        )


def test_dataplane_ports_are_uio_bound(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            (
                "sudo -n python3 - <<'PY'\n"
                "import json\n"
                "from pathlib import Path\n"
                "mapping = json.loads(Path('/etc/pulsaros/vxlan/pci-map.json').read_text())\n"
                "config = json.loads(Path('/etc/pulsaros/vxlan/config.json').read_text())\n"
                "mapped = {port['pci'] for port in mapping['ports']}\n"
                "configured = {port['pci'] for port in config['ports']}\n"
                "assert mapped == configured\n"
                "for port in mapping['ports']:\n"
                "    device = Path('/sys/bus/pci/devices') / port['pci']\n"
                "    assert (device / 'driver').resolve().name == 'uio_pci_generic'\n"
                "    assert not any((path / 'address').read_text().strip().lower() == port['mac'] for path in Path('/sys/class/net').iterdir())\n"
                "PY"
            ),
        )


def test_management_port_remains_kernel_owned(topology, ssh_user, ssh_key):
    resolved = topology["__resolved__"]
    for host in group_hosts(topology, "vteps"):
        management_macs = [
            nic["mac"].lower()
            for nic in resolved["hosts"][host]["nics"]
            if nic.get("management")
        ]
        assert management_macs
        for mac in management_macs:
            ssh(
                topology,
                ssh_user,
                ssh_key,
                host,
                f"test -n \"$(grep -il {mac} /sys/class/net/*/address)\"",
            )
