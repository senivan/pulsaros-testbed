import os
import re

from conftest import all_hosts, group_hosts, ssh


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


def test_custom_kernel_dpdk_profile_when_requested(topology, ssh_user, ssh_key):
    expected = os.environ.get("KERNEL_EXPECTED_RELEASE", "")
    profile = os.environ.get("KERNEL_DPDK_PROFILE", "none")
    if not expected or profile == "none":
        return

    expected_args = {
        "dpdk-vm": {
            "isolcpus=1-3",
            "nohz_full=1-3",
            "rcu_nocbs=1-3",
            "irqaffinity=0",
            "default_hugepagesz=2M",
            "hugepagesz=2M",
            "hugepages=2048",
        },
        "dpdk-small": {
            "isolcpus=2-3",
            "nohz_full=2-3",
            "rcu_nocbs=2-3",
            "irqaffinity=0-1",
            "default_hugepagesz=1G",
            "hugepagesz=1G",
            "hugepages=2",
        },
        "dpdk-bench": {
            "isolcpus=2-9",
            "nohz_full=2-9",
            "rcu_nocbs=2-9",
            "irqaffinity=0-1",
            "default_hugepagesz=1G",
            "hugepagesz=1G",
            "hugepages=8",
            "audit=0",
            "processor.max_cstate=1",
        },
    }
    assert profile in expected_args

    vteps = set(group_hosts(topology, "vteps"))
    assert vteps, "topology has no VTEP hosts to validate DPDK kernel profile"
    profile_args = expected_args[profile]
    managed_args = set().union(*expected_args.values())

    for host in all_hosts(topology):
        result = ssh(topology, ssh_user, ssh_key, host, "cat /proc/cmdline")
        cmdline_args = set(result.stdout.strip().split())
        if host in vteps:
            missing = profile_args - cmdline_args
            assert not missing, f"{host} missing {profile} kernel args: {sorted(missing)}"
        else:
            unexpected = managed_args & cmdline_args
            assert not unexpected, f"{host} unexpectedly has DPDK kernel args: {sorted(unexpected)}"


def test_dmesg_has_no_panic_or_oops(topology, ssh_user, ssh_key):
    forbidden = {
        "kernel panic": re.compile(r"\bkernel panic\b", re.IGNORECASE),
        "kernel oops": re.compile(r"\b(?:oops|kernel oops):\b", re.IGNORECASE),
    }
    for host in all_hosts(topology):
        result = ssh(topology, ssh_user, ssh_key, host, "sudo -n dmesg || dmesg")
        for label, pattern in forbidden.items():
            assert not pattern.search(result.stdout), f"{host} dmesg contains {label}"
