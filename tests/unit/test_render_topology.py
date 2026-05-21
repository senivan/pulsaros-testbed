import importlib.util
import os
import pathlib

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
