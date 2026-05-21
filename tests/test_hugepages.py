from conftest import group_hosts, ssh


def test_vteps_have_hugepages(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "awk '/HugePages_Total/ {print $2}' /proc/meminfo",
        )
        assert int(result.stdout.strip()) > 0


def test_vteps_have_huge_mountpoint(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        ssh(topology, ssh_user, ssh_key, host, "test -d /mnt/huge")
