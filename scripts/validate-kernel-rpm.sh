#!/usr/bin/env bash
set -euo pipefail

log() { printf '[kernel-rpm-validate] %s\n' "$*"; }
die() { printf '[kernel-rpm-validate] ERROR: %s\n' "$*" >&2; exit 1; }

RPM_DIR="${1:-artifacts/kernel-rpms}"
ROOT_FSTYPE="${KERNEL_ROOT_FSTYPE:-xfs}"
REQUIRE_LVM_INITRAMFS="${KERNEL_REQUIRE_LVM_INITRAMFS:-1}"

if command -v rpm2cpio >/dev/null 2>&1; then
  EXTRACTOR="rpm2cpio"
elif command -v bsdtar >/dev/null 2>&1; then
  EXTRACTOR="bsdtar"
else
  die "rpm2cpio or bsdtar is required"
fi

if [[ "$EXTRACTOR" == "rpm2cpio" ]]; then
  command -v cpio >/dev/null 2>&1 || die "cpio is required"
fi

[[ -d "$RPM_DIR" ]] || die "RPM directory not found: $RPM_DIR"

shopt -s nullglob
rpms=("$RPM_DIR"/kernel-*.rpm)
(( ${#rpms[@]} > 0 )) || die "No kernel RPMs found in $RPM_DIR"

case "$ROOT_FSTYPE" in
  xfs) root_symbol="CONFIG_XFS_FS" ;;
  btrfs) root_symbol="CONFIG_BTRFS_FS" ;;
  ext4) root_symbol="CONFIG_EXT4_FS" ;;
  *) die "Unsupported root filesystem for validation: $ROOT_FSTYPE" ;;
esac

require_builtin_or_module() {
  local config="$1"
  local symbol="$2"
  if ! grep -Eq "^${symbol}=(y|m)$" "$config"; then
    die "Missing required kernel option ${symbol}=y/m in $config"
  fi
}

initramfs_contains() {
  local initramfs="$1"
  local pattern="$2"
  if command -v lsinitrd >/dev/null 2>&1; then
    lsinitrd "$initramfs" | grep -Eq "$pattern"
  elif command -v bsdtar >/dev/null 2>&1; then
    bsdtar -tf "$initramfs" | grep -Eq "$pattern"
  else
    die "lsinitrd or bsdtar is required to inspect initramfs contents"
  fi
}

validate_rpm() {
  local rpm="$1"
  local rpm_abs
  local tmpdir
  rpm_abs="$(cd "$(dirname "$rpm")" && pwd)/$(basename "$rpm")"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  log "Extracting $(basename "$rpm")"
  (
    cd "$tmpdir"
    if [[ "$EXTRACTOR" == "rpm2cpio" ]]; then
      rpm2cpio "$rpm_abs" | cpio -id --quiet
    else
      bsdtar -xf "$rpm_abs"
    fi
  )

  local configs=("$tmpdir"/boot/config-*)
  (( ${#configs[@]} == 1 )) || die "Expected exactly one /boot/config-* in $(basename "$rpm"), found ${#configs[@]}"

  local config="${configs[0]}"
  local release="${config##*/config-}"
  local initramfs="$tmpdir/boot/initramfs-${release}.img"

  [[ -f "$initramfs" ]] || die "Missing /boot/initramfs-${release}.img in $(basename "$rpm")"

  require_builtin_or_module "$config" CONFIG_BLK_DEV_INITRD
  require_builtin_or_module "$config" CONFIG_DEVTMPFS
  require_builtin_or_module "$config" CONFIG_NET
  require_builtin_or_module "$config" CONFIG_INET
  require_builtin_or_module "$config" CONFIG_NETDEVICES
  require_builtin_or_module "$config" CONFIG_VIRTIO
  require_builtin_or_module "$config" CONFIG_VIRTIO_PCI
  require_builtin_or_module "$config" CONFIG_VIRTIO_NET
  require_builtin_or_module "$config" "$root_symbol"

  if grep -q '^CONFIG_SCSI_VIRTIO=y$' "$config"; then
    :
  elif grep -q '^CONFIG_SCSI_VIRTIO=m$' "$config"; then
    initramfs_contains "$initramfs" 'virtio_scsi' || \
      die "CONFIG_SCSI_VIRTIO=m but virtio_scsi is missing from /boot/initramfs-${release}.img"
  else
    die "Missing CONFIG_SCSI_VIRTIO=y/m for Proxmox virtio-scsi boot"
  fi

  if [[ "$REQUIRE_LVM_INITRAMFS" == "1" ]]; then
    initramfs_contains "$initramfs" '(^|/)(lvm|lvm_scan|dmsetup)( |$)' || \
      die "LVM initramfs support is required for the Fedora Proxmox template but is missing from /boot/initramfs-${release}.img"
  fi

  log "PASS: $(basename "$rpm") supports Proxmox virtio-scsi boot with $ROOT_FSTYPE root"
  rm -rf "$tmpdir"
  trap - RETURN
}

for rpm in "${rpms[@]}"; do
  validate_rpm "$rpm"
done
