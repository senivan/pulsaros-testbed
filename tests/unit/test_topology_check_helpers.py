import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "test_topology_checks.py"


def load_module():
    spec = importlib.util.spec_from_file_location("topology_checks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_decoded_capture_assertions_pass_with_vxlan_evidence():
    module = load_module()
    module._assert_decoded_capture(
        "\n".join(
            [
                "PULSAROS_PACKET_COUNT=2",
                "IP 172.16.100.1.4789 > 172.16.100.2.4789: VXLAN, flags [I], vni 10100",
                "IP 10.10.10.11 > 10.10.10.12: ICMP echo request",
            ]
        ),
        {
            "min_packets": 1,
            "vxlan_vni": 10100,
            "inner_ips": ["10.10.10.11", "10.10.10.12"],
            "outer_ips": ["172.16.100.1", "172.16.100.2"],
            "contains": ["VXLAN"],
            "not_contains": ["unreachable"],
        },
        "capture",
    )


def test_decoded_capture_assertions_fail_for_missing_pattern():
    module = load_module()
    with pytest.raises(pytest.fail.Exception):
        module._assert_decoded_capture(
            "PULSAROS_PACKET_COUNT=1\nIP 10.10.10.11 > 10.10.10.12: ICMP echo request",
            {"vxlan_vni": 10100},
            "capture",
        )


def test_decoded_capture_assertions_fail_for_forbidden_pattern():
    module = load_module()
    with pytest.raises(pytest.fail.Exception):
        module._assert_decoded_capture(
            "PULSAROS_PACKET_COUNT=1\nICMP unreachable",
            {"not_contains": ["unreachable"]},
            "capture",
        )


def test_parse_ping_metrics_extracts_loss_and_rtt():
    module = load_module()
    metrics = module._parse_ping_metrics(
        "\n".join(
            [
                "3 packets transmitted, 2 received, 33.3333% packet loss, time 2002ms",
                "rtt min/avg/max/mdev = 0.313/0.393/0.474/0.080 ms",
            ]
        )
    )

    assert metrics["packets_transmitted"] == 3
    assert metrics["packets_received"] == 2
    assert metrics["packet_loss_percent"] == 33.3333
    assert metrics["rtt_avg_ms"] == 0.393


def test_metric_warnings_are_report_only_strings():
    module = load_module()
    warnings = module._metric_warnings(
        {"packet_loss_percent": 1.0, "rtt_avg_ms": 2.5},
        {"max_loss_percent": 0, "max_rtt_avg_ms": 10},
    )

    assert warnings == ["packet_loss_percent=1.0 exceeds max_loss_percent=0.0"]


def test_parse_pktgen_metrics_extracts_best_effort_stats():
    module = load_module()
    metrics = module._parse_pktgen_metrics(
        "Tx Pkts: 1,024\nRx Pkts: 1,000\nTx pps: 512.5\nobytes: 65536"
    )

    assert metrics["tx_packets"] == 1024
    assert metrics["rx_packets"] == 1000
    assert metrics["tx_pps"] == 512.5
    assert metrics["tx_bytes"] == 65536
