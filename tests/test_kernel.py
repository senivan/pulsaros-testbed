from conftest import ssh


def test_all_vms_respond_to_ssh(topology, ssh_user, ssh_key):
    for host in ("client-a", "client-b", "vtep-a", "vtep-b"):
        ssh(topology, ssh_user, ssh_key, host, "true")


def test_uname_readable(topology, ssh_user, ssh_key):
    for host in ("client-a", "client-b", "vtep-a", "vtep-b"):
        result = ssh(topology, ssh_user, ssh_key, host, "uname -r")
        assert result.stdout.strip()


def test_dmesg_has_no_panic_or_oops(topology, ssh_user, ssh_key):
    forbidden = ("kernel panic", "oops")
    for host in ("client-a", "client-b", "vtep-a", "vtep-b"):
        result = ssh(topology, ssh_user, ssh_key, host, "sudo -n dmesg || dmesg")
        lower = result.stdout.lower()
        for needle in forbidden:
            assert needle not in lower, f"{host} dmesg contains {needle}"
