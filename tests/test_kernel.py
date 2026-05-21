import os
import re

from conftest import all_hosts, ssh


def test_all_vms_respond_to_ssh(topology, ssh_user, ssh_key):
    for host in all_hosts(topology):
        ssh(topology, ssh_user, ssh_key, host, "true")


def test_uname_readable(topology, ssh_user, ssh_key):
    for host in all_hosts(topology):
        result = ssh(topology, ssh_user, ssh_key, host, "uname -r")
        assert result.stdout.strip()


def test_custom_kernel_marker_when_requested(topology, ssh_user, ssh_key):
    expected = os.environ.get("KERNEL_EXPECTED_RELEASE", "")
    if not expected:
        return
    for host in all_hosts(topology):
        result = ssh(topology, ssh_user, ssh_key, host, "uname -r")
        assert expected in result.stdout.strip()


def test_dmesg_has_no_panic_or_oops(topology, ssh_user, ssh_key):
    forbidden = {
        "kernel panic": re.compile(r"\bkernel panic\b", re.IGNORECASE),
        "kernel oops": re.compile(r"\b(?:oops|kernel oops):\b", re.IGNORECASE),
    }
    for host in all_hosts(topology):
        result = ssh(topology, ssh_user, ssh_key, host, "sudo -n dmesg || dmesg")
        for label, pattern in forbidden.items():
            assert not pattern.search(result.stdout), f"{host} dmesg contains {label}"
