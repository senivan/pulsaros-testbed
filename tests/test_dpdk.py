import pytest

from conftest import group_hosts, ssh


def test_dpdk_testpmd_available_or_report_unavailable(topology, ssh_user, ssh_key):
    unavailable = []
    for host in group_hosts(topology, "vteps"):
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "command -v dpdk-testpmd || command -v testpmd",
            check=False,
        )
        if result.returncode != 0:
            unavailable.append(host)

    if unavailable:
        pytest.skip(f"DPDK testpmd unavailable on: {', '.join(unavailable)}")
