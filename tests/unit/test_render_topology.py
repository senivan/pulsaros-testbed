import importlib.util
import os
import pathlib
import textwrap

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "render-topology.py"
TOPOLOGY = ROOT / "topologies" / "linux-vxlan-reference.yml"


spec = importlib.util.spec_from_file_location("render_topology", MODULE_PATH)
render_topology = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_topology)


pytestmark = pytest.mark.skipif(
    render_topology.yaml is None,
    reason="python3-yaml is required for topology renderer tests",
)


def base_env(monkeypatch):
    monkeypatch.setenv("RUN_ID", "123456")
    monkeypatch.setenv("NETWORK_MODE", "qinq")
    monkeypatch.setenv("MGMT_BRIDGE", "vmbr0")
    monkeypatch.setenv("SDN_BRIDGE", "vmbr-test")
    monkeypatch.setenv("QINQ_SERVICE_VLAN_BASE", "3000")
    monkeypatch.setenv("QINQ_SERVICE_VLAN_COUNT", "500")


def test_default_topology_renders_legacy_compat(monkeypatch):
    base_env(monkeypatch)

    data = render_topology.render(TOPOLOGY)

    assert data["name"] == "linux-vxlan-reference"
    assert data["hosts"]["client-a"]["vmid"] == 223457
    assert data["hosts"]["vtep-b"]["vm_name"] == "pulsar-123456-vtep-b"
    assert data["networks"]["left-l2"]["vnet"] == "pl123456"
    assert data["networks"]["underlay"]["inner_vlan"] == 102
    assert data["qinq"]["zone"] == "pq123456"
    assert data["qinq"]["service_vlan"] == 3456
    assert data["checks"][0]["name"] == "overlay-ping"
    assert data["checks"][1]["captures"][0]["nic"] == "underlay"
    assert data["checks"][2]["name"] == "pktgen-client-a-to-b"
    assert data["checks"][2]["destination_mac"] == data["hosts"]["client-b"]["nics"][1]["mac"]


def test_default_topology_resolves_ansible_vars(monkeypatch):
    base_env(monkeypatch)
    data = render_topology.render(TOPOLOGY)
    client_a = data["hosts"]["client-a"]

    dataplane_mac = render_topology.resolve_token(
        data, client_a, client_a["ansible_vars"]["dataplane_mac"]
    )
    peer_mac = render_topology.resolve_token(
        data, client_a, client_a["ansible_vars"]["peer_mac"]
    )

    assert dataplane_mac == data["hosts"]["client-a"]["nics"][1]["mac"]
    assert peer_mac == data["hosts"]["client-b"]["nics"][1]["mac"]


def test_bridge_mode_uses_legacy_vlan_tags(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("NETWORK_MODE", "bridge")
    monkeypatch.setenv("TEST_BRIDGE", "vmbr-test")

    data = render_topology.render(TOPOLOGY)

    assert data["networks"]["left-l2"]["bridge"] == "vmbr-test,tag=3457"
    assert data["networks"]["underlay"]["bridge"] == "vmbr-test,tag=3458"
    assert data["networks"]["right-l2"]["bridge"] == "vmbr-test,tag=3459"
    assert "qinq" not in data


def write_topology(tmp_path, checks):
    path = tmp_path / "topology.yml"
    path.write_text(
        textwrap.dedent(
            f"""
            schema_version: 1
            name: bad-checks
            networks:
              dataplane:
                mode: access
                vnet_prefix: pd
                inner_vlan: 101
            hosts:
              host-a:
                vmid_offset: 1
                groups: []
                nics:
                  - name: mgmt
                    network: management
                    mac_offset: 1
                    management: true
                  - name: data
                    network: dataplane
                    mac_offset: 11
            checks:
            {checks}
            """
        ),
        encoding="utf-8",
    )
    return path


def test_check_rejects_unknown_host(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: bad-ping
                  type: ping
                  source: missing-host
                  destination: 10.10.0.2
                """
            ).strip(),
            "  ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_packet_capture_rejects_unknown_nic(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: bad-capture
                  type: packet_capture
                  captures:
                    - host: host-a
                      nic: missing-nic
                      filter: udp port 4789
                  trigger:
                    type: ping
                    source: host-a
                    destination: 10.10.0.2
                """
            ).strip(),
            "  ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_pktgen_dpdk_rejects_missing_destination_mac(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: bad-pktgen
                  type: pktgen_dpdk
                  source: host-a
                  nic: data
                  source_ip: 10.10.0.1/24
                  destination_ip: 10.10.0.2
                """
            ).strip(),
            "  ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)
