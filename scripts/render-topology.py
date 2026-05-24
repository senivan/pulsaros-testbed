#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
INVENTORY = ROOT / "ansible" / "inventory.generated.ini"
PLAYBOOK = ROOT / "ansible" / "site.generated.yml"
TOPOLOGY_JSON = ARTIFACTS / "topology.json"
TOPOLOGY_ENV = ARTIFACTS / "topology.env"


def die(message):
    print(f"render-topology: ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def env_int(name, default=None):
    value = os.environ.get(name)
    if value in (None, ""):
        if default is None:
            die(f"{name} is required")
        return default
    if not re.fullmatch(r"[0-9]+", value):
        die(f"{name} must be numeric")
    return int(value)


def env_str(name, default=None):
    value = os.environ.get(name)
    if value in (None, ""):
        if default is None:
            die(f"{name} is required")
        return default
    return value


def mac_for(run_id, offset):
    return "52:54:%02x:%02x:%02x:%02x" % (
        (run_id >> 16) & 255,
        (run_id >> 8) & 255,
        run_id & 255,
        offset,
    )


def load_yaml(path):
    if yaml is None:
        print("render-topology: python3-yaml is required", file=sys.stderr)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        die(f"{path} must contain a YAML mapping")
    return data


def validate_unique(values, label):
    seen = set()
    for value in values:
        if value in seen:
            die(f"duplicate {label}: {value}")
        seen.add(value)


def validate_host_ref(hosts, host_name, label):
    if host_name not in hosts:
        die(f"{label} references unknown host {host_name}")


def validate_host_nic_ref(hosts, host_name, nic_name, label):
    validate_host_ref(hosts, host_name, label)
    if not any(nic["name"] == nic_name for nic in hosts[host_name]["nics"]):
        die(f"{label} references unknown nic {host_name}.{nic_name}")


def validate_ping_check(hosts, check, label):
    source = check.get("source")
    destination = check.get("destination")
    if not source:
        die(f"{label} must define source")
    if not destination:
        die(f"{label} must define destination")
    validate_host_ref(hosts, source, label)


def validate_packet_capture_check(hosts, check, label):
    captures = check.get("captures")
    trigger = check.get("trigger")
    if not isinstance(captures, list) or not captures:
        die(f"{label} must define captures")
    for index, capture in enumerate(captures):
        capture_label = f"{label} capture {index}"
        if not isinstance(capture, dict):
            die(f"{capture_label} must be a mapping")
        host = capture.get("host")
        nic = capture.get("nic")
        if not host:
            die(f"{capture_label} must define host")
        if not nic:
            die(f"{capture_label} must define nic")
        if not capture.get("filter"):
            die(f"{capture_label} must define filter")
        validate_host_nic_ref(hosts, host, nic, capture_label)
    if not isinstance(trigger, dict):
        die(f"{label} must define trigger")
    if trigger.get("type") != "ping":
        die(f"{label} trigger type must be ping")
    validate_ping_check(hosts, trigger, f"{label} trigger")


def validate_pktgen_dpdk_check(hosts, check, label):
    source = check.get("source")
    nic = check.get("nic")
    if not source:
        die(f"{label} must define source")
    if not nic:
        die(f"{label} must define nic")
    validate_host_nic_ref(hosts, source, nic, label)
    for field in ("destination_mac", "source_ip", "destination_ip"):
        if not check.get(field):
            die(f"{label} must define {field}")
    for field in ("source_ip", "destination_ip"):
        if "/" in str(check[field]):
            die(f"{label} {field} must be an address, not a CIDR")
    protocol = check.get("protocol", "udp")
    if protocol not in ("udp", "tcp", "icmp"):
        die(f"{label} protocol must be udp, tcp, or icmp")
    packet_size = int(check.get("packet_size", 128))
    if packet_size < 64 or packet_size > 1518:
        die(f"{label} packet_size must be between 64 and 1518")
    rate_percent = int(check.get("rate_percent", 1))
    if rate_percent < 1 or rate_percent > 100:
        die(f"{label} rate_percent must be between 1 and 100")
    duration = int(check.get("duration", 5))
    if duration < 1:
        die(f"{label} duration must be at least 1 second")


def validate_checks(hosts, checks):
    if checks in (None, []):
        return []
    if not isinstance(checks, list):
        die("checks must be a list")
    names = []
    for index, check in enumerate(checks):
        label = f"check {index}"
        if not isinstance(check, dict):
            die(f"{label} must be a mapping")
        name = check.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            die(f"{label} must define a valid name")
        names.append(name)
        check_type = check.get("type")
        if check_type == "ping":
            validate_ping_check(hosts, check, f"check {name}")
        elif check_type == "packet_capture":
            validate_packet_capture_check(hosts, check, f"check {name}")
        elif check_type == "pktgen_dpdk":
            validate_pktgen_dpdk_check(hosts, check, f"check {name}")
        else:
            die(f"check {name} has unsupported type {check_type}")
    validate_unique(names, "check name")
    return checks


def render(topology_path, previous=None):
    source = load_yaml(topology_path)
    if source.get("schema_version") != 1:
        die("only topology schema_version 1 is supported")

    run_id = env_int("RUN_ID")
    network_mode = env_str("NETWORK_MODE", "qinq")
    if network_mode not in ("qinq", "bridge"):
        die(f"NETWORK_MODE must be qinq or bridge, got: {network_mode}")

    qinq_service_vlan_base = env_int("QINQ_SERVICE_VLAN_BASE", 3000)
    qinq_service_vlan_count = env_int("QINQ_SERVICE_VLAN_COUNT", 500)
    if qinq_service_vlan_count <= 0:
        die("QINQ_SERVICE_VLAN_COUNT must be greater than zero")

    run_suffix = f"{run_id % 1000000:06d}"
    base = 200000 + run_id % 50000
    legacy_vlan_base = 3000 + run_id % 500

    raw_networks = source.get("networks")
    raw_hosts = source.get("hosts")
    if not isinstance(raw_networks, dict) or not raw_networks:
        die("topology must define networks")
    if not isinstance(raw_hosts, dict) or not raw_hosts:
        die("topology must define hosts")

    validate_unique(
        [net.get("vnet_prefix") for net in raw_networks.values()],
        "network vnet_prefix",
    )
    validate_unique(
        [host.get("vmid_offset") for host in raw_hosts.values()],
        "host vmid_offset",
    )

    networks = {}
    for name, network in raw_networks.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            die(f"invalid network name: {name}")
        mode = network.get("mode", "access")
        if mode not in ("access", "trunk"):
            die(f"network {name} mode must be access or trunk")
        prefix = network.get("vnet_prefix")
        if not isinstance(prefix, str) or not re.fullmatch(r"[a-z][a-z0-9]?", prefix):
            die(f"network {name} must define a one or two character vnet_prefix")
        inner_vlan = int(network.get("inner_vlan"))
        legacy_offset = int(network.get("legacy_vlan_offset", inner_vlan))
        vlan = inner_vlan if network_mode == "qinq" else legacy_vlan_base + legacy_offset
        vnet = f"{prefix}{run_suffix}" if network_mode == "qinq" else ""
        bridge = vnet if network_mode == "qinq" else f"{env_str('TEST_BRIDGE')},tag={vlan}"
        networks[name] = {
            "name": name,
            "mode": mode,
            "vnet_prefix": prefix,
            "vnet": vnet,
            "inner_vlan": inner_vlan,
            "legacy_vlan_offset": legacy_offset,
            "vlan": vlan,
            "bridge": bridge,
        }

    hosts = {}
    all_mac_offsets = []
    previous_hosts = {}
    if previous and previous.get("run_id") == run_id:
        previous_hosts = previous.get("hosts", {})
    for name, host in raw_hosts.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            die(f"invalid host name: {name}")
        vmid_offset = int(host.get("vmid_offset"))
        vmid = base + vmid_offset
        vm_name = f"pulsar-{run_id}-{name}"
        nics = []
        nic_names = []
        for nic in host.get("nics", []):
            nic_name = nic.get("name")
            network_name = nic.get("network")
            if network_name != "management" and network_name not in networks:
                die(f"host {name} nic {nic_name} references unknown network {network_name}")
            mac_offset = int(nic.get("mac_offset"))
            all_mac_offsets.append(mac_offset)
            nic_names.append(nic_name)
            bridge = env_str("MGMT_BRIDGE") if nic.get("management") else networks[network_name]["bridge"]
            nics.append(
                {
                    "name": nic_name,
                    "network": network_name,
                    "mac_offset": mac_offset,
                    "mac": mac_for(run_id, mac_offset),
                    "management": bool(nic.get("management", False)),
                    "bridge": bridge,
                }
            )
        validate_unique(nic_names, f"{name} nic name")
        hosts[name] = {
            "name": name,
            "vmid": vmid,
            "vm_name": vm_name,
            "groups": host.get("groups", []),
            "nics": nics,
            "ansible_vars": host.get("ansible_vars", {}),
            "management_ip": previous_hosts.get(name, {}).get("management_ip", ""),
        }
    validate_unique(all_mac_offsets, "mac_offset")
    resolved = {
        "schema_version": 1,
        "name": source["name"],
        "description": source.get("description", ""),
        "run_id": run_id,
        "run_suffix": run_suffix,
        "base": base,
        "network_mode": network_mode,
        "networks": networks,
        "hosts": hosts,
        "plays": source.get("plays", []),
        "compat": source.get("compat", {}),
    }
    if network_mode == "qinq":
        resolved["qinq"] = {
            "sdn_bridge": env_str("SDN_BRIDGE", env_str("TEST_BRIDGE", "vmbr-test")),
            "zone": f"pq{run_suffix}",
            "service_vlan": qinq_service_vlan_base + run_id % qinq_service_vlan_count,
            "mtu": env_int("QINQ_MTU", 1496),
            "ipam": env_str("QINQ_IPAM", "pve"),
        }
    checks = validate_checks(hosts, source.get("checks", []))
    resolved["checks"] = resolve_tokens(resolved, {}, checks)
    return resolved


def nic_by_name(host, nic_name):
    for nic in host["nics"]:
        if nic["name"] == nic_name:
            return nic
    die(f"host {host['name']} has no nic {nic_name}")


def resolve_token(data, current_host, value):
    if not isinstance(value, str) or not value.startswith("@"):
        return value
    if value.startswith("@nic:"):
        _, rest = value.split(":", 1)
        nic_name, field = rest.split(".", 1)
        return nic_by_name(current_host, nic_name)[field]
    if value.startswith("@host:"):
        _, rest = value.split(":", 1)
        host_name, field = rest.split(".", 1)
        host = data["hosts"][host_name]
        if field.startswith("nic:"):
            nic_name, nic_field = field[4:].split(".", 1)
            return nic_by_name(host, nic_name)[nic_field]
        return host.get(field, "")
    if value.startswith("@network:"):
        _, rest = value.split(":", 1)
        network_name, field = rest.split(".", 1)
        return data["networks"][network_name].get(field, "")
    die(f"unknown topology reference: {value}")


def resolve_tokens(data, current_host, value):
    if isinstance(value, dict):
        return {
            key: resolve_tokens(data, current_host, item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [resolve_tokens(data, current_host, item) for item in value]
    return resolve_token(data, current_host, value)


def write_json(data):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    TOPOLOGY_JSON.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_env(data):
    compat = {
        "RUN_ID": data["run_id"],
        "TOPOLOGY": data["name"],
        "NETWORK_MODE": data["network_mode"],
        "BASE": data["base"],
    }
    if data["network_mode"] == "qinq":
        compat.update(
            {
                "SDN_BRIDGE": data["qinq"]["sdn_bridge"],
                "QINQ_ZONE": data["qinq"]["zone"],
                "QINQ_SERVICE_VLAN": data["qinq"]["service_vlan"],
                "QINQ_MTU": data["qinq"]["mtu"],
            }
        )
    for key, value in data.get("compat", {}).items():
        compat[key] = resolve_token(data, {}, value)
    lines = [f"{key}={value}" for key, value in compat.items() if value not in (None, "")]
    TOPOLOGY_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_inventory(data):
    groups = {}
    for host in data["hosts"].values():
        for group in host.get("groups", []):
            groups.setdefault(group, []).append(host)

    user = os.environ.get("ANSIBLE_USER", "pulsar")
    key = os.environ.get("ANSIBLE_SSH_PRIVATE_KEY_FILE", "~/.ssh/pulsaros-testbed")
    lines = []
    for group in sorted(groups):
        lines.append(f"[{group}]")
        for host in groups[group]:
            vars_out = {
                "ansible_host": host["management_ip"],
            }
            for key_name, value in host.get("ansible_vars", {}).items():
                vars_out[key_name] = resolve_token(data, host, value)
            rendered = " ".join(f"{name}={value}" for name, value in vars_out.items())
            lines.append(f"{host['name']} {rendered}")
        lines.append("")
    lines.extend(
        [
            "[all:vars]",
            f"ansible_user={user}",
            f"ansible_ssh_private_key_file={key}",
            "ansible_ssh_common_args='-o StrictHostKeyChecking=no'",
            "",
        ]
    )
    INVENTORY.write_text("\n".join(lines), encoding="utf-8")


def write_playbook(data):
    PLAYBOOK.write_text(yaml.safe_dump(data["plays"], sort_keys=False), encoding="utf-8")


def load_resolved():
    if not TOPOLOGY_JSON.exists():
        die("artifacts/topology.json not found")
    return json.loads(TOPOLOGY_JSON.read_text(encoding="utf-8"))


def cmd_render(args):
    previous = None
    if TOPOLOGY_JSON.exists():
        previous = load_resolved()
    data = render(args.topology_file, previous=previous)
    write_json(data)
    write_env(data)


def cmd_update_ips(args):
    data = load_resolved()
    for item in args.host_ip:
        if "=" not in item:
            die(f"expected host=ip, got {item}")
        host, ip = item.split("=", 1)
        if host not in data["hosts"]:
            die(f"unknown host for IP update: {host}")
        data["hosts"][host]["management_ip"] = ip
    write_json(data)
    write_env(data)


def cmd_ansible(_args):
    data = load_resolved()
    missing = [host["name"] for host in data["hosts"].values() if not host.get("management_ip")]
    if missing:
        die(f"missing management IPs: {', '.join(missing)}")
    write_inventory(data)
    write_playbook(data)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--topology-file", required=True)
    render_parser.set_defaults(func=cmd_render)
    update_parser = sub.add_parser("update-ips")
    update_parser.add_argument("host_ip", nargs="+")
    update_parser.set_defaults(func=cmd_update_ips)
    ansible_parser = sub.add_parser("ansible")
    ansible_parser.set_defaults(func=cmd_ansible)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
