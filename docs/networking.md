# Networking

The Proxmox host must provide a management bridge and a parent bridge for
testbed traffic:

```text
vmbr0       management bridge
vmbr-test  QinQ SDN parent bridge
```

Scripts do not create Linux bridges dynamically. In the default `qinq` mode,
they do create and delete per-run Proxmox SDN QinQ zones and VNets on top of
the existing parent bridge.

Topology files define the logical dataplane networks. The renderer maps those
logical networks to either generated QinQ VNets or legacy bridge VLAN tags.

## Default QinQ Mode

`NETWORK_MODE=qinq` is the default. Each run creates one QinQ zone and one VNet
per topology network. The default topology creates:

```text
zone:       pq<RUN_ID % 1000000>
left VNet:  pl<RUN_ID % 1000000>
underlay:   pu<RUN_ID % 1000000>
right VNet: pr<RUN_ID % 1000000>
```

The generated QinQ zone uses an outer service VLAN:

```bash
QINQ_SERVICE_VLAN=$(( QINQ_SERVICE_VLAN_BASE + RUN_ID % QINQ_SERVICE_VLAN_COUNT ))
```

The default topology VNets use fixed inner VLANs:

```text
left-l2:   101
underlay:  102
right-l2:  103
```

VM NICs attach directly to the generated VNets. Topology networks can be marked
as `access` or `trunk`; the current renderer preserves that metadata and uses
the same generated VNet attachment path for both. Future scenarios can use the
metadata to decide whether guest traffic should be untagged or tagged inside the
VM.

The cleanup path deletes the generated VNets and zone after the VMs are
destroyed. Generated SDN names are intentionally short because Proxmox bridge
device names are limited.

## Legacy Bridge Mode

`NETWORK_MODE=bridge` keeps the old behavior. The Proxmox host must provide a
VLAN-aware `TEST_BRIDGE`, and each run gets three host-side VLANs:

```bash
VLAN_BASE=$(( 3000 + RUN_ID % 500 ))
LEFT_VLAN=$((VLAN_BASE + 1))
UNDERLAY_VLAN=$((VLAN_BASE + 2))
RIGHT_VLAN=$((VLAN_BASE + 3))
```

VM NICs attach to `TEST_BRIDGE` with Proxmox `tag=<vlan>`.

## Default NIC Layout

The default `linux-vxlan-reference` topology declares this NIC layout:

```text
client-a:
  net0 = management
  net1 = left-l2

vtep-a:
  net0 = management
  net1 = left-l2
  net2 = underlay

vtep-b:
  net0 = management
  net1 = underlay
  net2 = right-l2

client-b:
  net0 = management
  net1 = right-l2
```

Linux VXLAN reference values:

```text
client-a dataplane: 10.10.0.1/24
client-b dataplane: 10.10.0.2/24
vtep-a underlay:    172.16.100.1/30
vtep-b underlay:    172.16.100.2/30
VNI:                100
UDP port:           4789
```

The default topology also declares a `pktgen_dpdk` check from `client-a` on its
left dataplane NIC toward `client-b`. The check uses Pktgen-DPDK with the DPDK
AF_PACKET PMD, so the client NIC remains visible to Linux and does not need to
be rebound to a userspace driver.

## Adding Networks

Add new logical networks in a topology YAML file under `networks`. Each network
needs a short `vnet_prefix`, an `inner_vlan`, and `mode: access` or
`mode: trunk`.

Generated Proxmox bridge device names must stay short, so VNet prefixes should
normally be one or two lowercase characters. For a run suffix of `123456`, a
prefix `pa` becomes VNet `pa123456`.
