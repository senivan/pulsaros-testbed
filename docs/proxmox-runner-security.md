# Proxmox Runner Security

Do not run this workflow for public pull requests.

Do not run the self-hosted runner as root on the Proxmox host.

Do not give the runner passwordless sudo ALL.

Recommended v1 posture:

- Use a private repository.
- Restrict workflow execution to trusted maintainers.
- Give the runner access only to the Proxmox operations needed for this repository.
- Use a fixed template VMID and fixed bridge/storage inputs.
- Keep Proxmox root credentials out of the repository and GitHub secrets.
- Review `scripts/pve-destroy-run.sh` before expanding cleanup behavior.

Destructive scripts verify generated VM names before deletion. Stale cleanup is dry-run by default and requires `--yes` to delete.
