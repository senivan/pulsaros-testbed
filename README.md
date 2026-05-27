# pulsaros-testbed

Disposable Proxmox CI testbed for PulsarOS kernel and DPDK VXLAN development.

The GitHub runner is only the orchestrator. Each run renders a topology, creates fresh Proxmox VMs, provisions them with Ansible, runs the selected pytest scenario, collects logs and pcaps, then destroys the generated resources unless explicitly told to keep them for debugging.

## What It Does

- Clones disposable VMs from one Proxmox template using `qm`.
- Reads topology-as-code from `topologies/*.yml`.
- Defaults to this Linux VXLAN reference topology:

  ```text
  client-a -- vtep-a -- underlay -- vtep-b -- client-b
  ```

- Uses `vmbr0` by default for management.
- Uses Proxmox SDN QinQ by default for disposable dataplane networks on top of `vmbr-test`.
- Renders per-run state into `artifacts/topology.json` and compatibility values into `artifacts/topology.env`.
- Tracks lifecycle progress and generated resource state in `artifacts/run-state.json`.
- Generates `ansible/inventory.generated.ini` and `ansible/site.generated.yml`.
- Can build PulsarOS kernel RPMs on a GitHub-hosted runner and install them into the Proxmox VMs.
- Runs kernel, hugepage, DPDK availability, and Linux VXLAN reference tests.
- Uploads artifacts from `artifacts/`, `logs/`, `pcaps/`, and `junit/`, plus optional Gemini artifact analysis.

## What It Does Not Do Yet

The repo does not yet implement custom ISO generation, a PulsarOS installer, OVS-DPDK, SR-IOV, PCI passthrough, multi-node Proxmox scheduling, a web dashboard, or custom DPDK VXLAN app validation.

## Security Warning

Do not run this workflow for public pull requests.

Do not run the GitHub self-hosted runner as root on the Proxmox host.

Prefer a restricted runner user or a runner VM/LXC that controls Proxmox through a limited interface.

Do not give the runner passwordless sudo ALL.

## Proxmox Host Requirements

- Proxmox VE host with `qm`, `pvesh`, `pvesm`, `pvecm`, and `/etc/pve`.
- Existing template VM, default VMID `9000`.
- Existing management bridge, default `vmbr0`.
- Existing SDN parent bridge, default `vmbr-test`.
- Proxmox SDN support available through `pvesh`.
- Runner user can run the required `qm`, `pvesh`, and `pvesm` operations through a restricted mechanism.
- Runner has `ansible-playbook`, `pytest`, `ssh`, `scp`, `jq`, and `python3-yaml`.

## Template VM Requirements

The template VM must have:

- `qemu-guest-agent` installed, enabled, and working.
- SSH enabled.
- A non-root SSH user, default `pulsar`.
- The runner SSH public key installed for that user.
- Package manager access for Ansible roles.
- Virtio NIC support.

The testbed requires qemu guest agent for management IP discovery.

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
make scenario SCENARIO=topology-checks
make logs
make destroy
```

If a run fails before cleanup:

```bash
RUN_ID=<failed-run-id> make destroy
```

The default dataplane mode is `NETWORK_MODE=qinq`. Each run creates temporary
Proxmox SDN QinQ objects with generated names like `pq123456`, `pl123456`,
`pu123456`, and `pr123456`, then deletes them during destroy. The setup phase
does touch Proxmox SDN, but the realized test networks are disposable and
per-run. To use the older single VLAN-aware bridge behavior, set:

```bash
export NETWORK_MODE=bridge
export TEST_BRIDGE=vmbr-test
```

## Topology-as-Code

Topologies are YAML files under `topologies/`. The default file is:

```text
topologies/linux-vxlan-reference.yml
```

Topology YAML declares:

- Logical networks and their generated VNet prefixes.
- Hosts, VMID offsets, groups, and NICs.
- Optional VXLAN `segments` that bind client LAN members to static multi-VTEP
  meshes.
- Ansible host variables.
- The generated playbook roles.
- Scenario acceptance checks such as pings and packet captures.
- Compatibility aliases for older scripts.

To add a new topology, add a YAML file under `topologies/` and run with:

```bash
export TOPOLOGY=<file-name-without-.yml>
```

Validate a topology without touching Proxmox or generated artifacts:

```bash
./scripts/render-topology.py validate --topology-file topologies/linux-vxlan-reference.yml
```

The create step renders `artifacts/topology.json`, `artifacts/topology.env`,
`ansible/inventory.generated.ini`, and `ansible/site.generated.yml`.

Topology-specific tests should be declared in the topology `checks:` section.
The generic pytest executor currently supports `ping`, `packet_capture`, and
`pktgen_dpdk` checks, plus `segment_ping_matrix` checks for topology-declared
client LAN segments, so new topologies do not need new fixed-host pytest
modules for basic connectivity and traffic generation validation.

The sample `linux-vxlan-3vtep-3lan` topology exercises a static three-VTEP
VXLAN mesh with three client LAN segments. It includes access clients and trunk
clients with guest VLAN subinterfaces.

## GitHub Actions Run

Open:

```text
Actions -> Proxmox PulsarOS Testbed -> Run workflow
```

Default useful inputs:

```text
topology=linux-vxlan-reference
scenario=topology-checks
network_mode=qinq
sdn_bridge=vmbr-test
kernel_source=none
```

The workflow always attempts log collection, VM destruction, and artifact upload. Set `keep_vms_on_failure=true` when debugging boot or provisioning failures.

## Custom Kernel Runs

The workflow can build PulsarOS kernel RPMs on a GitHub-hosted runner, then install them into the disposable Proxmox VMs before running tests.

Use:

```text
kernel_source=pulsaros-kernel-git
kernel_repo=https://github.com/senivan/PulsarOS-kernel.git
kernel_ref=main
kernel_version=7.0.9
```

Flow:

```text
ubuntu-latest builds RPMs from PulsarOS-kernel
self-hosted Proxmox runner downloads RPM artifact
Ansible copies RPMs to all VMs
VMs install RPMs, rewrite the PulsarOS kernel boot entry with the template kernel's known-good root arguments, reboot, and verify uname -r
scenario tests run against the custom kernel
```

For a direct RPM test, use `kernel_source=rpm-url` and provide `kernel_rpm_url`. For manual local runs, place RPMs in `artifacts/kernel-rpms/` and export:

```bash
export KERNEL_SOURCE=pulsaros-kernel-git
export KERNEL_EXPECTED_RELEASE=pulsaros
make provision
```

The testbed intentionally reuses the template kernel's known-good root-related boot arguments on the installed PulsarOS kernel. This avoids inheriting package defaults like `rootfstype=ext4` when the Fedora template actually boots from another filesystem such as xfs, btrfs, or LVM-backed roots.

For kernel boot debugging, set:

```text
keep_vms_on_failure=true
```

When a custom kernel panics or SSH never returns, the workflow will collect what it can and leave the generated VMs available for Proxmox console inspection. Clean them up manually with `RUN_ID=<run-id> make destroy` after debugging.

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

The cleanup scripts only target generated names like `pulsar-<run-id>-client-a`
and generated QinQ SDN names derived from the same run ID.

## Artifacts

- `artifacts/topology.json`: canonical resolved topology for the run.
- `artifacts/topology.env`: compatibility values for existing tests and shell scripts.
- `artifacts/run-state.json`: lifecycle phase and generated resource status.
- `ansible/inventory.generated.ini`: generated Ansible inventory.
- `ansible/site.generated.yml`: generated Ansible playbook.
- `logs/`: dmesg, journal, ip link, ip addr, and uname output.
- `pcaps/`: VXLAN underlay captures from VTEPs.
- `junit/`: pytest JUnit XML reports.
- `artifacts/ai-analysis.md`: local artifact summary plus optional Gemini analysis.

## Gemini Artifact Analysis

The workflow uploads raw testbed artifacts first, then a separate `analyze-artifacts` job runs on a GitHub-hosted `ubuntu-latest` runner. That job downloads the uploaded artifacts, runs `scripts/analyze-artifacts-gemini.py`, and uploads a separate AI analysis artifact.

The analyzer always writes a local summary to `artifacts/ai-analysis.md`.

To enable Gemini analysis, add a repository secret:

```text
GEMINI_API_KEY
```

Optionally set a repository variable:

```text
GEMINI_MODEL=gemini-2.5-flash
GEMINI_MAX_OUTPUT_TOKENS=4096
```

If no API key is configured, the analyzer skips the model call and the workflow continues. The analysis is also written to the GitHub Actions job summary for the `analyze-artifacts` job, so you can read it without downloading the artifact archive.
