# Architecture

Each test run creates four full-clone VMs from a Proxmox template:

```text
pulsar-${RUN_ID}-client-a
pulsar-${RUN_ID}-vtep-a
pulsar-${RUN_ID}-vtep-b
pulsar-${RUN_ID}-client-b
```

The runner computes deterministic VM IDs:

```bash
BASE=$(( 200000 + RUN_ID % 50000 ))
CLIENT_A=$((BASE + 1))
VTEP_A=$((BASE + 2))
VTEP_B=$((BASE + 3))
CLIENT_B=$((BASE + 4))
```

The run state is written to `artifacts/topology.env`. Every later script reads that file instead of rediscovering topology.

The GitHub runner does not host the test workload. It only calls Proxmox `qm`, runs Ansible over SSH, invokes pytest, and uploads artifacts.

v1 intentionally uses the `qm` CLI only. Proxmox API support is out of scope.
