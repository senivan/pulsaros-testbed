# Networking

The Proxmox host must provide two bridges:

```text
vmbr0       management bridge
vmbr-test  VLAN-aware test bridge
```

Scripts do not create bridges dynamically.

Each run gets three VLANs:

```bash
VLAN_BASE=$(( 3000 + RUN_ID % 500 ))
LEFT_VLAN=$((VLAN_BASE + 1))
UNDERLAY_VLAN=$((VLAN_BASE + 2))
RIGHT_VLAN=$((VLAN_BASE + 3))
```

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
