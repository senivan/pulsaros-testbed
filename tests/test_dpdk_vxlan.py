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
                "PY"
            ),
        )
