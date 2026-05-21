#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

cat >&2 <<'EOF'
This repository does not fully automate Fedora template creation yet.

Create a Proxmox template VM manually with:
- qemu-guest-agent installed and enabled
- SSH enabled
- a non-root user, default: pulsar
- the runner SSH public key installed for that user
- sudo rights limited to required provisioning commands
- virtio NIC support

Then mark it as a template and pass its VMID as TEMPLATE_ID/template_vmid.
EOF
