# pulsaros-testbed

Disposable Proxmox CI testbed for PulsarOS kernel and DPDK VXLAN development.

The GitHub runner is only the orchestrator. Each run creates fresh Proxmox VMs, provisions them with Ansible, runs the selected pytest scenario, collects logs and pcaps, then destroys the VMs even if the scenario fails.

## What v1 Does

- Clones four VMs from one Proxmox template using `qm`.
- Builds this topology:

  ```text
  client-a -- vtep-a -- underlay -- vtep-b -- client-b
  ```

- Uses existing Proxmox bridges:
  - `vmbr0` for management
  - `vmbr-test` for VLAN-tagged dataplane networks
- Runs kernel, hugepage, DPDK availability, and Linux VXLAN reference tests.
- Uploads artifacts from `artifacts/`, `logs/`, `pcaps/`, and `junit/`.

## What v1 Does Not Do

v1 does not implement custom ISO generation, a PulsarOS installer, OVS-DPDK, SR-IOV, PCI passthrough, Proxmox API support, multi-node scheduling, a web dashboard, or custom DPDK VXLAN app validation.

## Security Warning

Do not run this workflow for public pull requests.

Do not run the GitHub self-hosted runner as root on the Proxmox host.

Prefer a restricted runner user or a runner VM/LXC that controls Proxmox through a limited interface.

Do not give the runner passwordless sudo ALL.

## Proxmox Host Requirements

- Proxmox VE host with `qm`, `pvesm`, `pvecm`, and `/etc/pve`.
- Existing template VM, default VMID `9000`.
- Existing management bridge, default `vmbr0`.
- Existing VLAN-aware test bridge, default `vmbr-test`.
- Runner user can run the required `qm` operations through a restricted mechanism.
- Runner has `ansible-playbook`, `pytest`, `ssh`, `scp`, and `jq`.

## Template VM Requirements

The template VM must have:

- `qemu-guest-agent` installed, enabled, and working.
- SSH enabled.
- A non-root SSH user, default `pulsar`.
- The runner SSH public key installed for that user.
- Package manager access for Ansible roles.
- Virtio NIC support.

v1 requires qemu guest agent for management IP discovery.

## Runner Labels

The GitHub Actions self-hosted runner must have:

```text
self-hosted
linux
proxmox
pulsaros-testbed
```

## Manual Run

```bash
cp .env.example .env
set -a
. ./.env
set +a

export RUN_ID="$(date +%s)"
make preflight
make create
make wait-ssh
make inventory
make provision
make scenario SCENARIO=linux-vxlan-reference
make logs
make destroy
```

If a run fails before cleanup:

```bash
RUN_ID=<failed-run-id> make destroy
```

## GitHub Actions Run

Open:

```text
Actions -> Proxmox PulsarOS Testbed -> Run workflow
```

Use scenario:

```text
linux-vxlan-reference
```

The workflow always attempts log collection, VM destruction, and artifact upload.

## Triggering From Another Repository Later

Use a private repository and trigger this workflow with `workflow_dispatch` through the GitHub API or `gh workflow run`. Do not expose Proxmox-backed runners to untrusted pull requests.

## Cleanup

Per-run cleanup:

```bash
./scripts/pve-destroy-run.sh "$RUN_ID"
```

Stale cleanup dry run:

```bash
./scripts/cleanup-stale-runs.sh --older-than-hours 24
```

Delete stale generated VMs:

```bash
./scripts/cleanup-stale-runs.sh --older-than-hours 24 --yes
```

The cleanup scripts only target generated names like `pulsar-<run-id>-client-a`.

## Artifacts

- `artifacts/topology.env`: generated VM IDs, VLANs, MACs, and IPs.
- `logs/`: dmesg, journal, ip link, ip addr, and uname output.
- `pcaps/`: VXLAN underlay captures from VTEPs.
- `junit/`: pytest JUnit XML reports.
