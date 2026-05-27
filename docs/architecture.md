# Architecture

Each test run renders a topology YAML file, then creates full-clone VMs from a
Proxmox template. The default topology in `topologies/linux-vxlan-reference.yml`
creates:

```text
pulsar-${RUN_ID}-client-a
pulsar-${RUN_ID}-vtep-a
pulsar-${RUN_ID}-vtep-b
pulsar-${RUN_ID}-client-b
```

The renderer computes deterministic VM IDs from each host's topology-declared
`vmid_offset`:

```bash
BASE=$(( 200000 + RUN_ID % 50000 ))
CLIENT_A=$((BASE + 1))
VTEP_A=$((BASE + 2))
VTEP_B=$((BASE + 3))
CLIENT_B=$((BASE + 4))
```

The run state is written to:

```text
artifacts/topology.json  canonical resolved topology
artifacts/topology.env   compatibility values for existing tests/scripts
artifacts/run-state.json lifecycle phase and generated resource status
```

The inventory step renders:

```text
ansible/inventory.generated.ini
ansible/site.generated.yml
```

Add new topologies as YAML files under `topologies/`. A topology declares
networks, hosts, NICs, Ansible host variables, generated playbook roles, and
scenario acceptance checks. Compatibility aliases are still rendered for older
scripts, but topology-specific pytest assertions should consume the resolved
JSON and the topology-declared `checks:` section instead of hard-coding host
names. The generic topology check runner supports ping checks, tcpdump-backed
packet capture checks, and client-side `pktgen_dpdk` traffic generation checks.

Topologies may also declare `segments` for topology-driven Linux VXLAN
configuration. Each segment defines one VNI, participating VTEPs, local VTEP
LAN NICs, and access or trunk client members. The `vxlan-test` Ansible role
reads the resolved topology JSON and configures static VXLAN flood entries
between all VTEPs in a segment.

The GitHub runner does not host the test workload. It only calls Proxmox tools,
runs Ansible over SSH, invokes pytest, and uploads artifacts.

VM lifecycle uses the Proxmox `qm` CLI. The default QinQ dataplane mode also
uses `pvesh` to create, apply, and delete generated Proxmox SDN zones and VNets.
Lifecycle scripts update `artifacts/run-state.json` so failed or interrupted
runs can be inspected and rerun without relying only on step logs.
