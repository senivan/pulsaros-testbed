# PulsarOS Testbed

PulsarOS Testbed is a reproducible Proxmox-based experimentation harness for
validating PulsarOS kernel builds, Linux VXLAN behavior, DPDK availability, and
multi-node network failure scenarios inside disposable virtual machines.

The repository treats a CI run as a controlled systems experiment. A topology is
rendered from source-controlled YAML, materialized as short-lived Proxmox VMs,
configured with Ansible, tested through topology-declared assertions, reduced to
artifacts, and then destroyed. The intent is to make kernel and networking
changes measurable, repeatable, and reviewable instead of relying on manual lab
state.

## Abstract

Modern kernel and dataplane work is difficult to validate with unit tests alone.
Useful evidence often requires booting the kernel, configuring multiple hosts,
building overlay networks, observing packet behavior, and proving recovery from
faults. This project provides that validation layer for PulsarOS by combining:

- Proxmox-backed disposable infrastructure
- topology-as-code for VM, NIC, VXLAN, VLAN, and test intent
- custom kernel RPM build and guest boot validation
- pytest-based experiment execution
- pcap, tcpdump, JUnit, run-state, performance, and failure artifacts
- optional AI-assisted artifact triage after the raw evidence is uploaded

The current system focuses on Linux VXLAN and custom kernel confidence. It is
not yet an installer, ISO factory, OVS-DPDK platform, SR-IOV lab, PCI passthrough
validator, or custom DPDK VXLAN dataplane test suite.

## Design Goals

The testbed is built around five principles.

1. **Reproducibility**
   Every run is derived from a topology file, workflow inputs, and a run ID.
   Generated VM IDs, Proxmox SDN names, inventory, and compatibility values are
   recorded as artifacts.

2. **Isolation**
   Test VMs and dataplane networks are disposable. QinQ SDN objects are created
   per run and removed during cleanup.

3. **Topology Agnosticism**
   Tests should not assume a fixed two-host topology. The same pytest executor
   can evaluate two VTEPs, three VTEPs, access LANs, trunk VLAN clients, and
   future segment layouts declared in YAML.

4. **Evidence Before Interpretation**
   The workflow uploads raw artifacts first: JUnit XML, logs, pcaps, decoded
   tcpdump output, topology JSON, run-state, performance metrics, and fault
   results. Human or AI analysis is layered on top of that evidence.

5. **Failure As A First-Class Case**
   A successful overlay is not enough. The testbed can intentionally break a
   path, verify the outage is observable, restore configuration, and prove that
   traffic recovers.

## System Architecture

The GitHub self-hosted runner is only an orchestrator. It does not host the
test workload. It drives Proxmox, Ansible, SSH, pytest, and artifact collection.

```text
GitHub Actions
  |
  | workflow_dispatch inputs
  v
self-hosted Proxmox runner
  |
  | render topology
  | create VMs and SDN objects
  | provision guests
  | execute pytest scenarios
  | collect artifacts
  v
Proxmox VE
  |
  +-- disposable VTEP VMs
  +-- disposable client VMs
  +-- generated QinQ SDN VNets
```

Each run follows this lifecycle:

```text
render topology
create Proxmox VMs and SDN objects
wait for qemu guest agent and SSH
generate Ansible inventory and playbook
provision guests
run selected scenario
collect logs, pcaps, JUnit, and experiment artifacts
destroy generated resources
analyze uploaded artifacts
```

The main generated files are:

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

## Topology Model

Topologies live under `topologies/` and define both infrastructure and
experiment intent. A topology can declare:

- logical Proxmox dataplane networks
- hosts, VMID offsets, groups, and NICs
- generated Ansible plays
- VXLAN `segments`
- success-path `checks`
- injected `faults`
- compatibility aliases for legacy scripts

The reference topology is intentionally small:

```text
topologies/linux-vxlan-reference.yml

client-a -- vtep-a == underlay == vtep-b -- client-b
```

The larger topology is designed to exercise topology-agnostic behavior:

```text
topologies/linux-vxlan-3vtep-3lan.yml

VTEPs:       vtep-a, vtep-b, vtep-c
Segments:    red, blue, green
Clients:     access clients on red/blue
Trunks:      VLAN-tagged clients on green
Underlay:    shared VTEP underlay network
```

Validate a topology without touching Proxmox:

```bash
./scripts/render-topology.py validate --topology-file topologies/linux-vxlan-3vtep-3lan.yml
```

## VXLAN Experiment Semantics

The current VXLAN implementation is deliberately explicit. It uses Linux
bridges, Linux VXLAN devices, static VTEP flood entries, and `nolearning`.

For each topology segment, the Ansible role configures:

- one bridge named from the VNI, for example `br-10100`
- one VXLAN device named from the VNI, for example `vx-10100`
- one VNI per segment
- one underlay address per participating VTEP
- static FDB flood entries between VTEPs
- local access NICs or trunk VLAN subinterfaces for clients

The expected properties are:

- clients in the same segment can communicate across any participating VTEP
- underlay captures contain UDP/4789 VXLAN traffic
- decoded pcaps contain the expected VNI
- decoded pcaps preserve expected inner client IPs
- decoded pcaps use expected outer VTEP underlay IPs
- trunk clients communicate only on the declared VLAN
- injected faults affect the intended path
- restore operations recover the path without rebuilding the testbed

This makes the VXLAN overlay testable as a system, not just as a configuration
script that exits successfully.

## Evaluation Methodology

The repository separates three classes of evidence.

### 1. Smoke Evidence

Smoke tests prove that the guest environment is suitable for deeper networking
experiments:

- custom or stock kernel boots
- expected kernel release is running
- hugepages are available
- DPDK tooling is present
- required guest services and interfaces are visible

### 2. Success-Path Network Evidence

Topology `checks:` define normal expected behavior. Supported check types are:

- `ping`
- `packet_capture`
- `pktgen_dpdk`
- `segment_ping_matrix`
- `segment_bidirectional_capture`
- `segment_perf_probe`

`segment_bidirectional_capture` verifies more than pcap presence. It drives
traffic between segment members, captures underlay traffic, decodes the pcap
with tcpdump, and validates expected VXLAN evidence.

`segment_perf_probe` records report-only ping loss and RTT data in:

```text
artifacts/perf-metrics.json
```

Performance thresholds are advisory in the current implementation. They produce
warnings but do not fail CI.

### 3. Failure-Mode Evidence

Topology `faults:` define intentional faults. Fault tests run in the `full`
scenario and write:

```text
artifacts/fault-results.json
```

Current injected failures are:

- `remove_fdb_peer`: remove VXLAN forwarding state and expect overlay traffic to fail
- `mtu_mismatch`: lower MTU and expect large DF traffic to fail
- `vlan_mismatch`: move a trunk client onto the wrong VLAN and expect traffic to fail
- `bounce_vtep_underlay`: bring down a VTEP underlay interface and expect traffic to fail

For each fault, the expected sequence is:

```text
verify path is healthy
inject fault
verify expected outage
restore configuration
verify path recovers
record result
```

A fault test fails if the outage does not happen or if recovery does not happen.

## Custom Kernel Validation

When `kernel_source=pulsaros-kernel-git`, the workflow builds PulsarOS kernel
RPMs on a GitHub-hosted runner and installs them into the disposable Proxmox
guests before the scenario runs.

The kernel validation path:

```text
clone PulsarOS-kernel
build kernel RPMs
upload RPM artifact
download RPMs on Proxmox runner
install RPMs in every VM
reuse the template kernel's known-good root boot arguments
reboot guests
wait for SSH
verify uname -r contains the expected PulsarOS release
run scenario tests
```

The boot-argument reuse is intentional. It prevents generated kernel package
defaults from replacing the template's known-good root, filesystem, LVM, or
device-mapper arguments.

Direct RPM testing is also supported:

```text
kernel_source=rpm-url
kernel_rpm_url=<https://.../kernel.rpm>
```

For local testing with prebuilt RPMs:

```bash
export KERNEL_SOURCE=pulsaros-kernel-git
export KERNEL_EXPECTED_RELEASE=pulsaros
make provision
```

## GitHub Actions Usage

Run from:

```text
Actions -> Proxmox PulsarOS Testbed -> Run workflow
```

Recommended full multi-VTEP custom-kernel inputs:

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

Faster reference run:

```text
scenario=topology-checks
topology=linux-vxlan-reference
kernel_source=none
```

The workflow attempts cleanup and artifact upload even after scenario failure.
Set `keep_vms_on_failure=true` when debugging guest boot, SSH, provisioning, or
kernel panic failures.

## Local Reproduction

Create a local environment:

```bash
cp .env.example .env
set -a
. ./.env
set +a
```

Run the reference topology:

```bash
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

Run the larger topology:

```bash
export TOPOLOGY=linux-vxlan-3vtep-3lan
export SCENARIO=full
```

If cleanup did not run:

```bash
RUN_ID=<failed-run-id> make destroy
```

## Artifacts

The main testbed artifact includes:

- `artifacts/topology.json`
- `artifacts/topology.env`
- `artifacts/run-state.json`
- `artifacts/perf-metrics.json`, when performance probes ran
- `artifacts/fault-results.json`, when fault tests ran
- `logs/*`
- `pcaps/*`
- `junit/*.xml`

The analysis job writes:

```text
artifacts/ai-analysis.md
artifacts/ai-analysis-input.json
```

The analyzer always produces a local summary. If `GEMINI_API_KEY` is configured,
it also asks Gemini for triage. If no API key is configured, the model call is
skipped and the analysis job still completes.

## Infrastructure Requirements

The Proxmox host needs:

- Proxmox VE with `qm`, `pvesh`, `pvesm`, `pvecm`, and `/etc/pve`
- a VM template, default `9000`
- a management bridge, default `vmbr0`
- a Proxmox SDN parent bridge, default `vmbr-test`
- SDN support available through `pvesh`

The runner needs:

- `ansible-playbook`
- `pytest`
- `ssh` and `scp`
- `jq`
- `python3-yaml`
- access to the required Proxmox commands through a restricted mechanism

The self-hosted GitHub runner must have these labels:

```text
self-hosted
linux
proxmox
pulsaros-testbed
```

The VM template needs:

- qemu guest agent installed and enabled
- SSH enabled
- a non-root SSH user, default `pulsar`
- the runner SSH public key installed for that user
- package manager access for Ansible roles
- virtio NIC support

## Networking Modes

Default mode is `NETWORK_MODE=qinq`.

In QinQ mode, each run creates disposable Proxmox SDN objects on top of
`SDN_BRIDGE`. Generated names are derived from the run suffix:

```text
pq123456   QinQ zone
pu123456   underlay VNet
ra123456   red-a VNet
```

The generated SDN objects are deleted during destroy.

Legacy bridge mode is available for hosts that already provide a VLAN-aware test
bridge:

```bash
export NETWORK_MODE=bridge
export TEST_BRIDGE=vmbr-test
```

## Safety And Threat Model

This repository controls real virtualization infrastructure. Treat it as a
privileged internal automation system.

Do not run this workflow for public pull requests.

Do not run the self-hosted runner as root on the Proxmox host. Prefer a
restricted runner user or an isolated runner VM/LXC with only the Proxmox
operations this repo needs. Do not give the runner passwordless `sudo ALL`.

The intended trust model is:

- workflow inputs are supplied by trusted maintainers
- topology files are reviewed before execution
- the runner can create and destroy only generated test resources
- secrets are not exposed to untrusted forks
- artifacts are treated as diagnostic output and reviewed before sharing

See `docs/proxmox-runner-security.md` for hardening guidance.

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

Cleanup targets generated names such as `pulsar-<run-id>-...` and the matching
generated QinQ SDN names.

## Limitations And Future Work

The current implementation establishes a reproducible foundation, but several
areas remain intentionally out of scope or future work:

- custom ISO generation and installer validation
- persistent multi-node Proxmox scheduling policy
- SR-IOV and PCI passthrough validation
- OVS-DPDK dataplane coverage
- custom DPDK VXLAN application validation
- hard performance gates based on historical baselines
- richer fault orchestration, such as VTEP reboot with automatic reprovisioning

## Related Documentation

- `docs/architecture.md`
- `docs/networking.md`
- `docs/troubleshooting.md`
- `docs/proxmox-runner-security.md`
