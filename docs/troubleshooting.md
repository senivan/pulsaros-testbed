# Troubleshooting

## Preflight Fails

Check that the workflow is running on the Proxmox-capable self-hosted runner and that `qm list` works for the runner user.

## Template Is Not Found

Confirm `TEMPLATE_ID` or the workflow `template_vmid` input. The VM must exist and be marked as a template.

## SSH Wait Times Out

v1 requires qemu guest agent for management IP discovery. Check:

- `qemu-guest-agent` is installed in the template.
- The guest agent service is enabled.
- Management networking provides an IP reachable from the runner.
- The configured SSH user and key are valid.

## VXLAN Ping Fails

Check `logs/*-ip-link.log`, `logs/*-ip-addr.log`, and `artifacts/topology.env`.

Common causes:

- In `qinq` mode, Proxmox SDN did not create or apply the generated VNets.
- In `bridge` mode, `vmbr-test` is not VLAN-aware.
- Dataplane NICs did not attach correctly.
- The template firewall blocks ICMP or VXLAN traffic.
- The VTEPs cannot reach each other on `172.16.100.0/30`.

For QinQ runs, `artifacts/topology.env` contains the generated zone and VNet
names. Check that those names exist on the Proxmox host and that the VM NICs are
attached to them.

## Cleanup

If the workflow fails before cleanup:

```bash
RUN_ID=<run-id> ./scripts/pve-destroy-run.sh "$RUN_ID"
```

For older generated VMs:

```bash
./scripts/cleanup-stale-runs.sh --older-than-hours 24
./scripts/cleanup-stale-runs.sh --older-than-hours 24 --yes
```
