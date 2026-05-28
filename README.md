# pulsaros-testbed

Disposable Proxmox testbed for PulsarOS kernel, DPDK, and Linux VXLAN validation.

The self-hosted GitHub runner is the orchestrator, not the workload host. A run
renders a topology, creates short-lived Proxmox VMs, provisions them with
Ansible, runs pytest scenarios over SSH, collects evidence, and destroys the
generated resources unless debugging is explicitly requested.

## What This Repo Is For

Use this repo when you need repeatable evidence that a PulsarOS kernel or
networking change works inside real VMs on Proxmox.

Current coverage includes:

- custom PulsarOS kernel RPM build, install, reboot, and `uname -r` validation
- topology-as-code for VM groups, NICs, networks, VXLAN segments, checks, and faults
- Proxmox SDN QinQ-backed disposable dataplane networks
- Linux VXLAN static multi-VTEP meshes
- access LANs and trunk VLAN client members
- kernel, hugepage, DPDK, ping, tcpdump, and pktgen-backed checks
- decoded VXLAN packet evidence from pcaps
- report-only performance/loss probes
- failure-mode tests with restore and recovery validation

It is not an installer, ISO builder, dashboard, SR-IOV harness, OVS-DPDK lab, or
custom DPDK VXLAN dataplane validator yet.

## Run Model

Every run follows the same lifecycle:

```text
render topology
create Proxmox VMs and SDN objects
wait for guest SSH
generate Ansible inventory and playbook
provision guests
run selected pytest scenario
collect logs, pcaps, JUnit, and analysis inputs
destroy generated resources
```

Generated state lives in:

```text
artifacts/topology.json       canonical resolved topology
artifacts/topology.env        compatibility environment for older scripts/tests
artifacts/run-state.json      lifecycle phase and generated resource state
ansible/inventory.generated.ini
ansible/site.generated.yml
junit/*.xml
logs/*
pcaps/*
```

## Topologies

Topologies are YAML files under `topologies/`.

The small reference topology is:

```text
topologies/linux-vxlan-reference.yml

client-a -- vtep-a == underlay == vtep-b -- client-b
```

The richer multi-VTEP topology is:

```text
topologies/linux-vxlan-3vtep-3lan.yml

3 VTEPs
3 VXLAN-backed LAN segments
access clients on red/blue
trunk VLAN clients on green
```

A topology declares:

- logical Proxmox dataplane networks
- hosts, VMID offsets, groups, and NICs
- Ansible plays and roles
- VXLAN `segments`
- scenario `checks`
- failure-mode `faults`
- compatibility aliases for older scripts

Validate a topology without touching Proxmox:

```bash
./scripts/render-topology.py validate --topology-file topologies/linux-vxlan-3vtep-3lan.yml
```

## VXLAN Expectations

The VXLAN model is intentionally explicit and static.

Each segment declares one VNI, participating VTEPs, VTEP underlay addresses,
local LAN NICs, and client members. The `vxlan-test` Ansible role configures:

- Linux bridges named from the VNI, for example `br-10100`
- Linux VXLAN devices named from the VNI, for example `vx-10100`
- `nolearning` VXLAN devices
- static flood FDB entries between every VTEP in the segment
- access client interfaces or trunk VLAN subinterfaces

Expected behavior:

- members of the same segment can reach each other across VTEPs
- underlay pcaps show UDP/4789 VXLAN traffic
- decoded pcaps show the expected VNI, outer VTEP IPs, and inner client IPs
- trunk members only work on the declared VLAN
- injected faults break the intended path
- restore actions recover the path without recreating the testbed

## Checks And Faults

Topology `checks:` are normal success-path assertions. Supported check types:

- `ping`
- `packet_capture`
- `pktgen_dpdk`
- `segment_ping_matrix`
- `segment_bidirectional_capture`
- `segment_perf_probe`

`segment_perf_probe` records report-only ping loss/RTT metrics in
`artifacts/perf-metrics.json`. Optional thresholds produce warnings, not CI
failures.

Topology `faults:` are destructive-but-restored failure tests. They run only in
the `full` scenario and write `artifacts/fault-results.json`.

Current fault types:

- `remove_fdb_peer`
- `mtu_mismatch`
- `vlan_mismatch`
- `bounce_vtep_underlay`

Fault tests are expected to observe an outage, run the restore path, and then
verify traffic recovers. A missing outage or failed recovery is a test failure.

## GitHub Actions

Run:

```text
Actions -> Proxmox PulsarOS Testbed -> Run workflow
```

Useful full multi-VTEP custom-kernel inputs:

```text
scenario=full
topology=linux-vxlan-3vtep-3lan
storage=DATA
network_mode=qinq
sdn_bridge=vmbr-test
kernel_source=pulsaros-kernel-git
kernel_repo=https://github.com/senivan/PulsarOS-kernel.git
kernel_ref=main
kernel_version=6.16
keep_vms_on_failure=false
```

For a faster reference run:

```text
scenario=topology-checks
topology=linux-vxlan-reference
kernel_source=none
```

The workflow always attempts log collection, VM destruction, artifact upload,
and optional Gemini analysis. Use `keep_vms_on_failure=true` when debugging
guest boot, kernel panic, SSH, or provisioning failures.

## Artifacts And Analysis

The main testbed artifact includes:

- `artifacts/topology.json`
- `artifacts/topology.env`
- `artifacts/run-state.json`
- `artifacts/perf-metrics.json`, when performance probes ran
- `artifacts/fault-results.json`, when fault tests ran
- `logs/`
- `pcaps/`
- `junit/`

The `analyze-artifacts` job downloads those artifacts on a GitHub-hosted runner
and writes:

```text
artifacts/ai-analysis.md
artifacts/ai-analysis-input.json
```

The analyzer always produces a local summary. If `GEMINI_API_KEY` is configured,
it also asks Gemini for failure triage. Without that secret, the model call is
skipped and the workflow still completes the analysis job.

## Custom Kernels

With `kernel_source=pulsaros-kernel-git`, the workflow builds kernel RPMs on a
GitHub-hosted runner, uploads them as an artifact, then the Proxmox runner
installs them into every guest.

The kernel role:

- copies RPMs into the VM
- installs them
- reuses the template kernel's known-good root-related boot arguments
- reboots
- waits for SSH
- verifies the expected PulsarOS kernel release

For direct RPM testing, use:

```text
kernel_source=rpm-url
kernel_rpm_url=<https://.../kernel.rpm>
```

For local manual testing, put RPMs under `artifacts/kernel-rpms/` and export:

```bash
export KERNEL_SOURCE=pulsaros-kernel-git
export KERNEL_EXPECTED_RELEASE=pulsaros
make provision
```

## Local Manual Run

```bash
cp .env.example .env
set -a
. ./.env
set +a

export RUN_ID="$(date +%s)"
export TOPOLOGY=linux-vxlan-reference
export SCENARIO=topology-checks

make preflight
make create
make wait-ssh
make inventory
make provision
make scenario
make logs
make destroy
```

For the larger topology:

```bash
export TOPOLOGY=linux-vxlan-3vtep-3lan
export SCENARIO=full
```

If cleanup did not run:

```bash
RUN_ID=<failed-run-id> make destroy
```

## Proxmox And Template Requirements

The Proxmox host needs:

- `qm`, `pvesh`, `pvesm`, `pvecm`, and `/etc/pve`
- a VM template, default `9000`
- a management bridge, default `vmbr0`
- a Proxmox SDN parent bridge, default `vmbr-test`
- SDN support available through `pvesh`
- `ansible-playbook`, `pytest`, `ssh`, `scp`, `jq`, and `python3-yaml` on the runner

The GitHub runner must have these labels:

```text
self-hosted
linux
proxmox
pulsaros-testbed
```

The template VM needs:

- qemu guest agent installed and enabled
- SSH enabled
- a non-root SSH user, default `pulsar`
- the runner SSH public key installed for that user
- package manager access for Ansible roles
- virtio NIC support

## Networking Modes

Default mode is `NETWORK_MODE=qinq`.

In QinQ mode each run creates disposable Proxmox SDN objects on top of
`SDN_BRIDGE`, with generated names such as:

```text
pq123456   QinQ zone
pu123456   underlay VNet
ra123456   red-a VNet
```

The objects are deleted during destroy.

Legacy bridge mode is still available:

```bash
export NETWORK_MODE=bridge
export TEST_BRIDGE=vmbr-test
```

In bridge mode, the selected bridge must already exist and be VLAN-aware.

## Safety

Do not run this workflow for public pull requests.

Do not run the self-hosted runner as root on the Proxmox host. Prefer a
restricted runner user or an isolated runner VM/LXC with only the Proxmox
operations this repo needs. Do not give the runner passwordless `sudo ALL`.

See `docs/proxmox-runner-security.md` for the hardening notes.

## Cleanup

Destroy one run:

```bash
./scripts/pve-destroy-run.sh "$RUN_ID"
```

Dry-run stale cleanup:

```bash
./scripts/cleanup-stale-runs.sh --older-than-hours 24
```

Delete stale generated resources:

```bash
./scripts/cleanup-stale-runs.sh --older-than-hours 24 --yes
```

Cleanup only targets generated names such as `pulsar-<run-id>-...` and the
matching generated QinQ SDN names.

## More Detail

- `docs/architecture.md`
- `docs/networking.md`
- `docs/troubleshooting.md`
- `docs/proxmox-runner-security.md`
