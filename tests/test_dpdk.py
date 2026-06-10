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


def test_pulsaros_vxlan_binary_deployed(topology, ssh_user, ssh_key):
    missing = []
    for host in group_hosts(topology, "vteps"):
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            "command -v pulsaros-vxlan-dp",
            check=False,
        )
        if result.returncode != 0:
            missing.append(host)

    assert not missing, f"pulsaros-vxlan-dp unavailable on: {', '.join(missing)}"


def test_pulsaros_vxlan_config_rendered(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            (
                "python3 - <<'PY'\n"
                "import json\n"
                "from pathlib import Path\n"
                "data = json.loads(Path('/etc/pulsaros/vxlan/config.json').read_text())\n"
                "assert data['schema_version'] == 1\n"
                "assert data['ports']\n"
                "assert data['vxlan']['segments']\n"
                "assert any(port['role'] == 'underlay' for port in data['ports'])\n"
                "assert any(port['role'] == 'access' for port in data['ports'])\n"
                "assert all(segment['access_ports'] for segment in data['vxlan']['segments'])\n"
                "PY"
            ),
        )


def test_pulsaros_vxlan_init_smoke_succeeded(topology, ssh_user, ssh_key):
    for host in group_hosts(topology, "vteps"):
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            (
                "test -s /tmp/pulsaros-testbed/pulsaros-vxlan-init.log && "
                "grep -q 'initialized .* DPDK ports' "
                "/tmp/pulsaros-testbed/pulsaros-vxlan-init.log && "
                "! grep -q 'failed to load config' "
                "/tmp/pulsaros-testbed/pulsaros-vxlan-init.log"
            ),
        )
