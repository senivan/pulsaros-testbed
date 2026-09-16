from conftest import dpdk_workload_hosts, ssh


def test_dpdk_workload_hosts_have_hugepages(topology, ssh_user, ssh_key):
    hosts = dpdk_workload_hosts(topology)
    assert hosts, "topology has no DPDK workload hosts"
    for host in hosts:
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "awk '/HugePages_Total/ {print $2}' /proc/meminfo",
        )
        assert int(result.stdout.strip()) > 0


def test_dpdk_workload_hosts_have_huge_mountpoint(topology, ssh_user, ssh_key):
    hosts = dpdk_workload_hosts(topology)
    assert hosts, "topology has no DPDK workload hosts"
    for host in hosts:
        ssh(topology, ssh_user, ssh_key, host, "test -d /mnt/huge")
