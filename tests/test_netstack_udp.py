import ipaddress
import json
import re
import shlex

from conftest import group_hosts, host_nic, iface_by_mac, ssh
from test_topology_checks import LOGS, _run_packet_capture_check


def udp_echo_script(source_ip, destination_ip):
    source = str(ipaddress.IPv4Address(source_ip))
    destination = str(ipaddress.IPv4Address(destination_ip))
    return f"""python3 - <<'PY'
import json
import socket

payload = b"hello-netstack"
destination = ({destination!r}, 9000)
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.settimeout(3)
    sock.bind(({source!r}, 0))
    assert sock.sendto(payload, destination) == len(payload)
    data, peer = sock.recvfrom(65535)
    assert data == payload, (data, payload)
    assert peer == destination, (peer, destination)
    print(json.dumps({{"source": sock.getsockname(), "peer": peer, "payload_hex": data.hex()}}))
PY
"""


def capture_assertions(source_ip, destination_ip):
    source = re.escape(source_ip)
    destination = re.escape(destination_ip)
    return {
        "min_packets": 4,
        "contains": [
            rf"Request who-has {destination}\b",
            rf"Reply {destination} is-at\b",
            rf"{source}\.\d+ > {destination}\.9000:",
            rf"{destination}\.9000 > {source}\.\d+:",
            r"\bUDP\b",
        ],
    }


def test_native_udp_echo_with_arp_capture(topology, ssh_user, ssh_key):
    clients = group_hosts(topology, "clients")
    netstacks = group_hosts(topology, "netstacks")
    assert len(clients) == len(netstacks) == 1
    client, netstack = clients[0], netstacks[0]
    hosts = topology["__resolved__"]["hosts"]
    source_ip = str(ipaddress.ip_interface(hosts[client]["ansible_vars"]["dataplane_ip"]).ip)
    destination_ip = str(ipaddress.ip_interface(hosts[netstack]["ansible_vars"]["netstack_ip"]).ip)
    nic = next(nic for nic in hosts[client]["nics"] if not nic.get("management"))
    iface = iface_by_mac(topology, ssh_user, ssh_key, client, host_nic(topology, client, nic["name"])["mac"])
    ssh(
        topology, ssh_user, ssh_key, netstack,
        "test -x /usr/local/libexec/pulsaros/netstack/udp-echo && "
        "sudo -n systemctl show pulsaros-netstack -p ExecStart --value | grep -Fq /udp-echo && "
        "sudo -n journalctl -b -u pulsaros-netstack --no-pager | grep -Fq 'udp_echo: bound UDP port 9000'",
    )
    ssh(
        topology, ssh_user, ssh_key, client,
        f"sudo -n ip neigh flush to {shlex.quote(destination_ip)} dev {shlex.quote(iface)}",
    )

    def trigger():
        result = ssh(topology, ssh_user, ssh_key, client,
                     udp_echo_script(source_ip, destination_ip), check=False, timeout=15)
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / "netstack-udp-echo-client.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        evidence = json.loads(result.stdout)
        assert evidence["peer"] == [destination_ip, 9000]
        assert evidence["payload_hex"] == b"hello-netstack".hex()

    _run_packet_capture_check(
        topology, ssh_user, ssh_key,
        {
            "name": "netstack-udp-capture",
            "captures": [{"host": client, "nic": nic["name"], "filter": "arp or udp port 9000"}],
            "assertions": capture_assertions(source_ip, destination_ip),
        },
        run_trigger=trigger,
    )
