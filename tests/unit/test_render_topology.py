import importlib.util
import os
import pathlib
import textwrap

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "render-topology.py"
TOPOLOGY = ROOT / "topologies" / "linux-vxlan-reference.yml"
MULTI_VTEP_TOPOLOGY = ROOT / "topologies" / "linux-vxlan-3vtep-3lan.yml"


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
    assert data["segments"]["default-lan"]["vni"] == 100
    assert data["segments"]["default-lan"]["vteps"][0]["underlay_address"] == "172.16.100.1"


def test_validate_command_does_not_require_run_environment(monkeypatch, capsys):
    for name in (
        "RUN_ID",
        "NETWORK_MODE",
        "MGMT_BRIDGE",
        "SDN_BRIDGE",
        "TEST_BRIDGE",
        "QINQ_SERVICE_VLAN_BASE",
        "QINQ_SERVICE_VLAN_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)

    render_topology.cmd_validate(type("Args", (), {"topology_file": TOPOLOGY})())

    assert "validated" in capsys.readouterr().out


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


def test_multi_vtep_topology_renders_segments(monkeypatch):
    base_env(monkeypatch)

    data = render_topology.render(MULTI_VTEP_TOPOLOGY)

    assert sorted(data["segments"]) == ["blue", "green", "red"]
    assert len(data["segments"]["red"]["vteps"]) == 3
    assert len(data["segments"]["red"]["members"]) == 3
    assert data["segments"]["green"]["members"][0]["mode"] == "trunk"
    assert data["segments"]["green"]["members"][0]["vlan"] == 30
    assert data["segments"]["red"]["vteps"][2]["underlay_address"] == "172.16.100.3"
    assert data["segments"]["red"]["bridge"] == "br-10100"
    assert data["segments"]["red"]["vxlan"] == "vx-10100"
    assert data["checks"][0]["type"] == "segment_ping_matrix"
    assert data["checks"][-1]["type"] == "segment_perf_probe"
    assert data["faults"][0]["type"] == "remove_fdb_peer"
    assert data["faults"][2]["type"] == "vlan_mismatch"


def write_topology(tmp_path, checks, extra=""):
    path = tmp_path / "topology.yml"
    base = textwrap.dedent(
        """
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
          host-b:
            vmid_offset: 2
            groups: []
            nics:
              - name: mgmt
                network: management
                mac_offset: 2
                management: true
              - name: data
                network: dataplane
                mac_offset: 12
        """
    )
    content = base
    if extra:
        content += textwrap.dedent(extra).strip() + "\n"
    content += "checks:\n" + checks + "\n"
    path.write_text(content, encoding="utf-8")
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


def test_packet_capture_accepts_decode_assertions(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: capture-with-assertions
                  type: packet_capture
                  captures:
                    - host: host-a
                      nic: data
                      filter: udp port 4789
                  trigger:
                    type: ping
                    source: host-a
                    destination: 10.10.0.2
                  assertions:
                    min_packets: 1
                    vxlan_vni: 100
                    inner_ips:
                      - 10.10.0.1
                      - 10.10.0.2
                    outer_ips:
                      - 172.16.0.1
                    contains:
                      - VXLAN
                    not_contains:
                      - "ICMP unreachable"
                """
            ).strip(),
            "  ",
        ),
    )

    data = render_topology.render(topology)

    assert data["checks"][0]["assertions"]["vxlan_vni"] == 100


def test_packet_capture_rejects_bad_assertions(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: bad-assertions
                  type: packet_capture
                  captures:
                    - host: host-a
                      nic: data
                      filter: udp port 4789
                  trigger:
                    type: ping
                    source: host-a
                    destination: 10.10.0.2
                  assertions:
                    min_packets: 0
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
                  source_ip: 10.10.0.1
                  destination_ip: 10.10.0.2
                """
            ).strip(),
            "  ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_pktgen_dpdk_rejects_cidr_source_ip(monkeypatch, tmp_path):
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
                  destination_mac: 52:54:00:00:00:02
                  source_ip: 10.10.0.1/24
                  destination_ip: 10.10.0.2
                """
            ).strip(),
            "  ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_segment_rejects_duplicate_vni(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        "  []",
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  first:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members: []
                  second:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members: []
                """
            ).strip(),
            "            ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_segment_trunk_member_requires_vlan(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        "  []",
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  trunk:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members:
                      - host: host-a
                        nic: data
                        mode: trunk
                        ip: 10.10.0.1/24
                """
            ).strip(),
            "            ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_segment_access_member_rejects_vlan(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        "  []",
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  access:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members:
                      - host: host-a
                        nic: data
                        mode: access
                        vlan: 10
                        ip: 10.10.0.1/24
                """
            ).strip(),
            "            ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_segment_rejects_conflicting_underlay_ip(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        "  []",
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  first:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members: []
                  second:
                    vni: 101
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.1.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members: []
                """
            ).strip(),
            "            ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_segment_bidirectional_capture_accepts_members(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: capture-segment
                  type: segment_bidirectional_capture
                  segment: overlay
                  captures:
                    - host: host-a
                      nic: data
                      filter: udp port 4789
                  pairs:
                    - source: host-a.data
                      destination: host-b.data
                """
            ).strip(),
            "  ",
        ),
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  overlay:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members:
                      - host: host-a
                        nic: data
                        mode: access
                        ip: 10.10.0.1/24
                      - host: host-b
                        nic: data
                        mode: access
                        ip: 10.10.0.2/24
                """
            ).strip(),
            "            ",
        ),
    )

    data = render_topology.render(topology)

    assert data["checks"][0]["type"] == "segment_bidirectional_capture"


def test_segment_bidirectional_capture_rejects_unknown_member(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: capture-segment
                  type: segment_bidirectional_capture
                  segment: overlay
                  captures:
                    - host: host-a
                      nic: data
                      filter: udp port 4789
                  pairs:
                    - source: host-a.data
                      destination: missing.data
                """
            ).strip(),
            "  ",
        ),
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  overlay:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members:
                      - host: host-a
                        nic: data
                        mode: access
                        ip: 10.10.0.1/24
                      - host: host-b
                        nic: data
                        mode: access
                        ip: 10.10.0.2/24
                """
            ).strip(),
            "            ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)


def test_segment_perf_probe_accepts_members(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        textwrap.indent(
            textwrap.dedent(
                """
                - name: perf-segment
                  type: segment_perf_probe
                  segment: overlay
                  count: 10
                  pairs:
                    - source: host-a.data
                      destination: host-b.data
                  thresholds:
                    max_loss_percent: 0
                    max_rtt_avg_ms: 10
                """
            ).strip(),
            "  ",
        ),
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  overlay:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members:
                      - host: host-a
                        nic: data
                        mode: access
                        ip: 10.10.0.1/24
                      - host: host-b
                        nic: data
                        mode: access
                        ip: 10.10.0.2/24
                """
            ).strip(),
            "            ",
        ),
    )

    data = render_topology.render(topology)

    assert data["checks"][0]["type"] == "segment_perf_probe"


def test_faults_accept_access_and_trunk_faults(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        "  []",
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  overlay:
                    vni: 100
                    vlan: 30
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members:
                      - host: host-a
                        nic: data
                        mode: trunk
                        vlan: 30
                        ip: 10.10.0.1/24
                      - host: host-b
                        nic: data
                        mode: trunk
                        vlan: 30
                        ip: 10.10.0.2/24
                faults:
                  - name: vlan-fault
                    type: vlan_mismatch
                    segment: overlay
                    fault_vlan: 31
                    pairs:
                      - source: host-a.data
                        destination: host-b.data
                  - name: underlay-bounce
                    type: bounce_vtep_underlay
                    segment: overlay
                    pairs:
                      - source: host-a.data
                        destination: host-b.data
                """
            ).strip(),
            "            ",
        ),
    )

    data = render_topology.render(topology)

    assert [fault["type"] for fault in data["faults"]] == [
        "vlan_mismatch",
        "bounce_vtep_underlay",
    ]


def test_fault_rejects_missing_pair(monkeypatch, tmp_path):
    base_env(monkeypatch)
    topology = write_topology(
        tmp_path,
        "  []",
        textwrap.indent(
            textwrap.dedent(
                """
                segments:
                  overlay:
                    vni: 100
                    vteps:
                      - host: host-a
                        underlay_nic: data
                        underlay_ip: 172.16.0.1/24
                      - host: host-b
                        underlay_nic: data
                        underlay_ip: 172.16.0.2/24
                    members:
                      - host: host-a
                        nic: data
                        mode: access
                        ip: 10.10.0.1/24
                      - host: host-b
                        nic: data
                        mode: access
                        ip: 10.10.0.2/24
                faults:
                  - name: bad-fault
                    type: remove_fdb_peer
                    segment: overlay
                """
            ).strip(),
            "            ",
        ),
    )

    with pytest.raises(SystemExit):
        render_topology.render(topology)
