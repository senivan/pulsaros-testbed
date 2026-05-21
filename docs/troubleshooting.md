# Troubleshooting

## Preflight Fails

Check that the workflow is running on the Proxmox-capable self-hosted runner and that `qm list` works for the runner user.

Topology rendering requires `python3-yaml` on the runner. On Fedora:

```bash
sudo dnf install -y python3-pyyaml
```

Also check that the selected topology exists:

```bash
test -f "topologies/${TOPOLOGY:-linux-vxlan-reference}.yml"
```

In QinQ mode, preflight must be able to read Proxmox SDN state:

```bash
sudo -n pvesh get /cluster/sdn/zones
sudo -n pvesh get /cluster/sdn/vnets
```

## Template Is Not Found

Confirm `TEMPLATE_ID` or the workflow `template_vmid` input. The VM must exist and be marked as a template.

## SSH Wait Times Out

The testbed requires qemu guest agent for management IP discovery. Check:

- `qemu-guest-agent` is installed in the template.
- The guest agent service is enabled.
- Management networking provides an IP reachable from the runner.
- The configured SSH user and key are valid.

## VXLAN Ping Fails

Check `logs/*-ip-link.log`, `logs/*-ip-addr.log`, `artifacts/topology.json`,
and `artifacts/topology.env`.

Common causes:

- In `qinq` mode, Proxmox SDN did not create or apply the generated VNets.
- In `bridge` mode, `vmbr-test` is not VLAN-aware.
- Dataplane NICs did not attach correctly.
- The template firewall blocks ICMP or VXLAN traffic.
- The VTEPs cannot reach each other on `172.16.100.0/30`.

For QinQ runs, `artifacts/topology.json` and `artifacts/topology.env` contain
the generated zone and VNet names. Check that those names exist on the Proxmox
host and that the VM NICs are attached to them.

## Inventory Generation Fails

Inventory generation requires every topology host to have a management IP in
`artifacts/topology.json`. If it fails, inspect the `Wait for SSH` step. It
should log the number of topology hosts and then one management IP per host.

If only some hosts get IPs, inspect Proxmox-side logs in `logs/*-qm-config.log`
and `logs/*-qm-status.log`. Log collection is designed to collect Proxmox data
even when guest SSH is unavailable.

## Custom Kernel Does Not Boot

Set `keep_vms_on_failure=true` and inspect the Proxmox console plus collected
serial and guest-agent logs. The kernel role reuses the template kernel's
known-good root arguments, but the custom kernel still needs built-in or
initramfs-available support for the template's storage stack, filesystem, LVM,
device mapper, virtio, and networking.

## Cleanup

If the workflow fails before cleanup:

```bash
RUN_ID=<run-id> ./scripts/pve-destroy-run.sh "$RUN_ID"
```

For older generated VMs and generated QinQ SDN objects:

```bash
./scripts/cleanup-stale-runs.sh --older-than-hours 24
./scripts/cleanup-stale-runs.sh --older-than-hours 24 --yes
```
