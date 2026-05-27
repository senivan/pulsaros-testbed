import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-state.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_state", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_topology_resources_records_vms_and_qinq_vnets():
    module = load_module()
    resources = module.topology_resources(
        {
            "network_mode": "qinq",
            "qinq": {"zone": "pq123456"},
            "hosts": {
                "client-a": {
                    "vmid": 223457,
                    "vm_name": "pulsar-123456-client-a",
                    "management_ip": "192.0.2.10",
                }
            },
            "networks": {
                "left-l2": {
                    "name": "left-l2",
                    "vnet": "pl123456",
                    "inner_vlan": 101,
                },
                "management": {
                    "name": "management",
                    "vnet": "",
                    "inner_vlan": 0,
                },
            },
        }
    )

    assert resources["vms"]["client-a"]["vmid"] == 223457
    assert resources["vms"]["client-a"]["status"] == "declared"
    assert resources["sdn"]["zone"] == "pq123456"
    assert resources["sdn"]["vnets"]["pl123456"]["inner_vlan"] == 101


def test_topology_resources_omits_sdn_for_bridge_mode():
    module = load_module()
    resources = module.topology_resources(
        {
            "network_mode": "bridge",
            "hosts": {},
            "networks": {
                "left-l2": {
                    "name": "left-l2",
                    "vnet": "",
                    "inner_vlan": 101,
                },
            },
        }
    )

    assert resources["sdn"]["zone"] == ""
    assert resources["sdn"]["vnets"] == {}
