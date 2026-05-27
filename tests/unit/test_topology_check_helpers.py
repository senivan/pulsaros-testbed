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
