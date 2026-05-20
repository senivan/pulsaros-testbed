import pathlib
import subprocess
import time

from conftest import HOSTS, PCAPS, iface_by_mac, scp_from, ssh


def test_client_a_can_ping_client_b_through_linux_vxlan(topology, ssh_user, ssh_key):
    ssh(topology, ssh_user, ssh_key, "client-a", "ping -c 3 -W 2 10.10.0.2", timeout=30)


def test_tcpdump_captures_udp_4789(topology, ssh_user, ssh_key):
    captures = {
        "vtep-a": (
            topology["VTEP_A_UNDERLAY_MAC"],
            "/tmp/pulsaros-testbed/vtep-a-underlay.pcap",
            PCAPS / "vtep-a-underlay.pcap",
        ),
        "vtep-b": (
            topology["VTEP_B_UNDERLAY_MAC"],
            "/tmp/pulsaros-testbed/vtep-b-underlay.pcap",
            PCAPS / "vtep-b-underlay.pcap",
        ),
    }

    capture_processes = []
    for host, (mac, remote_pcap, _) in captures.items():
        underlay_if = iface_by_mac(topology, ssh_user, ssh_key, host, mac)
        ssh(topology, ssh_user, ssh_key, host, f"sudo -n mkdir -p /tmp/pulsaros-testbed && sudo -n rm -f {remote_pcap}")
        args = [
            "ssh",
            "-i",
            str(ssh_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            f"{ssh_user}@{topology[HOSTS[host]]}",
            "sudo -n sh -c "
            f"'timeout 20 tcpdump -U -i {underlay_if} -w {remote_pcap} udp port 4789'",
        ]
        capture_processes.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    time.sleep(2)
    ssh(topology, ssh_user, ssh_key, "client-a", "ping -c 5 -W 2 10.10.0.2", timeout=40)
    time.sleep(3)

    for process in capture_processes:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    for host, (_, remote_pcap, local_pcap) in captures.items():
        ssh(
            topology,
            ssh_user,
            ssh_key,
            host,
            f"sudo -n chmod 0644 {remote_pcap} && test -s {remote_pcap}",
            timeout=30,
        )
        result = scp_from(topology, ssh_user, ssh_key, host, remote_pcap, local_pcap)
        assert result.returncode == 0, result.stderr
        assert pathlib.Path(local_pcap).stat().st_size > 0
