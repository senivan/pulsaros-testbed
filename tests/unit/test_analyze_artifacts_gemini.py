import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "analyze-artifacts-gemini.py"


def load_module():
    spec = importlib.util.spec_from_file_location("analyze_artifacts_gemini", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_topology_summary_keeps_segment_expectations():
    module = load_module()
    summary = module.topology_summary(
        {
            "name": "linux-vxlan-3vtep-3lan",
            "description": "test topology",
            "network_mode": "qinq",
            "hosts": {
                "blue-client-a": {
                    "groups": ["clients"],
                    "management_ip": "192.0.2.11",
                    "nics": [
                        {"name": "mgmt", "network": "management", "bridge": "vmbr0", "management": True},
                        {"name": "data", "network": "blue-a", "bridge": "ba123", "management": False},
                    ],
                }
            },
            "segments": {
                "blue": {
                    "vni": 10200,
                    "vlan": 20,
                    "vteps": [
                        {
                            "host": "vtep-a",
                            "underlay_nic": "underlay",
                            "underlay_ip": "172.16.100.1/24",
                            "local_nics": ["blue"],
                        }
                    ],
                    "members": [
                        {
                            "host": "blue-client-a",
                            "nic": "data",
                            "mode": "access",
                            "ip": "10.10.20.11/24",
                        }
                    ],
                }
            },
            "checks": [{"name": "blue-lan-ping-matrix", "type": "segment_ping_matrix", "segment": "blue"}],
        }
    )

    assert summary["name"] == "linux-vxlan-3vtep-3lan"
    assert summary["segments"]["blue"]["members"][0]["ip"] == "10.10.20.11/24"
    assert summary["checks"][0]["segment"] == "blue"


def test_gemini_prompt_uses_artifact_topology_not_legacy_reference():
    module = load_module()
    prompt = module.gemini_prompt(
        {
            "run": {"github_run_id": "1", "scenario": "full", "runner_name": "runner"},
            "topology": {
                "name": "linux-vxlan-3vtep-3lan",
                "segments": {
                    "blue": {
                        "members": [
                            {
                                "host": "blue-client-a",
                                "nic": "data",
                                "mode": "access",
                                "ip": "10.10.20.11/24",
                            }
                        ]
                    }
                },
            },
            "topology_env": "TOPOLOGY=linux-vxlan-3vtep-3lan\n",
            "artifact_text": {},
            "junit": [],
            "pcaps": [],
            "log_samples": {},
        }
    )

    assert "artifacts/topology.json" in prompt
    assert "linux-vxlan-3vtep-3lan" in prompt
    assert "10.10.20.11/24" in prompt
    assert "Treat this expected topology as authoritative" not in prompt
    assert "10.10.0.1/24" not in prompt
