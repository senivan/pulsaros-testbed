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

## Default QinQ Mode

`NETWORK_MODE=qinq` is the default. Each run creates:

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

The three generated VNets use fixed inner VLANs:

```text
left-l2:   101
underlay:  102
right-l2:  103
```

VM NICs attach directly to the generated VNets. The guest VTEPs can later use
their own VLAN tags inside the virtual traffic without colliding with the
run-isolation VLANs.

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

## NIC Layout

NIC layout:

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
