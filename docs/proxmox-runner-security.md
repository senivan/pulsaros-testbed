# Proxmox Runner Security

Do not run this workflow for public pull requests.

Do not run the self-hosted runner as root on the Proxmox host.

Do not give the runner passwordless sudo ALL.

Recommended posture:

- Use a private repository.
- Restrict workflow execution to trusted maintainers.
- Give the runner access only to the Proxmox operations needed for this repository.
- Use fixed allowlists for template VMID, storage, management bridge, SDN parent
  bridge, and legacy test bridge inputs.
- Keep Proxmox root credentials out of the repository and GitHub secrets.
- Review `scripts/pve-destroy-run.sh` before expanding cleanup behavior.

Current required Proxmox operations include VM lifecycle through `qm`, storage
checks through `pvesm`, and QinQ SDN create/apply/delete through `pvesh`.

Destructive scripts verify generated VM names before deletion. QinQ cleanup only
targets generated short SDN names derived from the run ID. Stale cleanup is
dry-run by default and requires `--yes` to delete.
