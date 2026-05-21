# Architecture

Each test run renders a topology YAML file, then creates full-clone VMs from a
Proxmox template. The default topology is:

```text
pulsar-${RUN_ID}-client-a
pulsar-${RUN_ID}-vtep-a
pulsar-${RUN_ID}-vtep-b
pulsar-${RUN_ID}-client-b
```

The renderer computes deterministic VM IDs:

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
```

The inventory step renders:

```text
ansible/inventory.generated.ini
ansible/site.generated.yml
```

Add new topologies as YAML files under `topologies/`. A topology declares
networks, hosts, NICs, Ansible host variables, and the generated playbook roles.
Scripts consume the resolved JSON instead of hard-coding host names where the
workflow is topology-generic.

The GitHub runner does not host the test workload. It only calls Proxmox `qm`, runs Ansible over SSH, invokes pytest, and uploads artifacts.

v1 intentionally uses the `qm` CLI only. Proxmox API support is out of scope.
