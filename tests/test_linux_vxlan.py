from conftest import group_hosts, ssh


def test_linux_vxlan_links_exist(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        result = ssh(topology, ssh_user, ssh_key, host, "ip -d -o link show type vxlan")
        assert result.stdout.strip(), f"no Linux VXLAN links found on {host}"


def test_pulsaros_dataplane_is_not_running(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "systemctl is-active --quiet pulsaros-vxlan",
            check=False,
        )
        assert result.returncode != 0, f"PulsarOS dataplane unexpectedly active on {host}"
