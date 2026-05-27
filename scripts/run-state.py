#!/usr/bin/env python3
"""Maintain per-run lifecycle state for disposable testbeds."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
TOPOLOGY_JSON = ARTIFACTS / "topology.json"
STATE_JSON = ARTIFACTS / "run-state.json"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def die(message: str) -> None:
    print(f"run-state: ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def topology_resources(topology: dict) -> dict:
    resources = {
        "vms": {},
        "sdn": {
            "zone": "",
            "vnets": {},
        },
    }
    for name, host in topology.get("hosts", {}).items():
        resources["vms"][name] = {
            "vmid": host.get("vmid"),
            "name": host.get("vm_name", ""),
            "management_ip": host.get("management_ip", ""),
            "status": "declared",
        }
    if topology.get("network_mode") == "qinq":
        resources["sdn"]["zone"] = topology.get("qinq", {}).get("zone", "")
        for network in topology.get("networks", {}).values():
            vnet = network.get("vnet", "")
            if vnet:
                resources["sdn"]["vnets"][vnet] = {
                    "network": network.get("name", ""),
                    "inner_vlan": network.get("inner_vlan"),
                    "status": "declared",
                }
    return resources


def load_state() -> dict:
    return read_json(STATE_JSON)


def save_state(state: dict) -> None:
    state["updated_at"] = now()
    write_json(STATE_JSON, state)


def ensure_state(run_id: str | None = None) -> dict:
    state = load_state()
    if state:
        if run_id and str(state.get("run_id", "")) != str(run_id):
            die(f"run-state RUN_ID {state.get('run_id')} does not match requested RUN_ID {run_id}")
        return state
    topology = read_json(TOPOLOGY_JSON)
    if not topology:
        die("artifacts/topology.json not found")
    state = {
        "schema_version": 1,
        "run_id": topology.get("run_id"),
        "topology": topology.get("name", ""),
        "network_mode": topology.get("network_mode", ""),
        "created_at": now(),
        "updated_at": now(),
        "phase": "initialized",
        "phases": [],
        "resources": topology_resources(topology),
    }
    if run_id and str(state["run_id"]) != str(run_id):
        die(f"topology RUN_ID {state['run_id']} does not match requested RUN_ID {run_id}")
    return state


def cmd_init(args: argparse.Namespace) -> None:
    topology = read_json(TOPOLOGY_JSON)
    if not topology:
        die("artifacts/topology.json not found")
    previous = load_state()
    if previous and str(previous.get("run_id", "")) != str(topology.get("run_id", "")):
        previous = {}
    previous_vms = previous.get("resources", {}).get("vms", {}) if previous else {}
    state = {
        "schema_version": 1,
        "run_id": topology.get("run_id"),
        "topology": topology.get("name", ""),
        "network_mode": topology.get("network_mode", ""),
        "created_at": previous.get("created_at", now()) if previous else now(),
        "updated_at": now(),
        "phase": args.phase,
        "phases": previous.get("phases", []) if previous else [],
        "resources": topology_resources(topology),
    }
    for name, vm in state["resources"]["vms"].items():
        if name in previous_vms:
            vm["status"] = previous_vms[name].get("status", vm["status"])
            vm["management_ip"] = previous_vms[name].get("management_ip", vm["management_ip"])
    state["phases"].append({"name": args.phase, "status": "started", "at": now()})
    save_state(state)


def cmd_phase(args: argparse.Namespace) -> None:
    state = ensure_state(args.run_id)
    state["phase"] = args.name
    state.setdefault("phases", []).append(
        {
            "name": args.name,
            "status": args.status,
            "at": now(),
            **({"message": args.message} if args.message else {}),
        }
    )
    save_state(state)


def cmd_vm(args: argparse.Namespace) -> None:
    state = ensure_state(args.run_id)
    vms = state.setdefault("resources", {}).setdefault("vms", {})
    vm = vms.setdefault(args.host, {})
    vm["status"] = args.status
    if args.management_ip:
        vm["management_ip"] = args.management_ip
    save_state(state)


def cmd_sdn(args: argparse.Namespace) -> None:
    state = ensure_state(args.run_id)
    sdn = state.setdefault("resources", {}).setdefault("sdn", {"zone": "", "vnets": {}})
    if args.kind == "zone":
        sdn["zone_status"] = args.status
    else:
        sdn.setdefault("vnets", {}).setdefault(args.name, {})["status"] = args.status
    save_state(state)


def cmd_sync_topology(args: argparse.Namespace) -> None:
    state = ensure_state(args.run_id)
    topology = read_json(TOPOLOGY_JSON)
    for name, host in topology.get("hosts", {}).items():
        vm = state.setdefault("resources", {}).setdefault("vms", {}).setdefault(name, {})
        vm["management_ip"] = host.get("management_ip", "")
    save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--phase", default="initialized")
    init.set_defaults(func=cmd_init)

    phase = sub.add_parser("phase")
    phase.add_argument("name")
    phase.add_argument("--status", default="completed")
    phase.add_argument("--message", default="")
    phase.add_argument("--run-id", default=os.environ.get("RUN_ID", ""))
    phase.set_defaults(func=cmd_phase)

    vm = sub.add_parser("vm")
    vm.add_argument("host")
    vm.add_argument("status")
    vm.add_argument("--management-ip", default="")
    vm.add_argument("--run-id", default=os.environ.get("RUN_ID", ""))
    vm.set_defaults(func=cmd_vm)

    sdn = sub.add_parser("sdn")
    sdn.add_argument("kind", choices=("zone", "vnet"))
    sdn.add_argument("name")
    sdn.add_argument("status")
    sdn.add_argument("--run-id", default=os.environ.get("RUN_ID", ""))
    sdn.set_defaults(func=cmd_sdn)

    sync = sub.add_parser("sync-topology")
    sync.add_argument("--run-id", default=os.environ.get("RUN_ID", ""))
    sync.set_defaults(func=cmd_sync_topology)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
