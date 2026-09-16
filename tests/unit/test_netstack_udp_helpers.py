import importlib.util
from pathlib import Path
import socket

import pytest

from test_topology_checks import _assert_decoded_capture


def load_module():
    path = Path(__file__).resolve().parents[1] / "test_netstack_udp.py"
    spec = importlib.util.spec_from_file_location("netstack_udp", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("bad", [None, "payload", "peer", "timeout"])
def test_socket_assertion_checks_payload_and_peer(monkeypatch, capsys, bad):
    module = load_module()

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            assert timeout == 3

        def bind(self, address):
            assert address == ("192.0.2.1", 0)

        def sendto(self, payload, destination):
            assert payload == b"hello-netstack"
            assert destination == ("192.0.2.2", 9000)
            return len(payload)

        def recvfrom(self, length):
            assert length == 65535
            if bad == "timeout":
                raise TimeoutError("missing UDP reply")
            return (b"corrupt" if bad == "payload" else b"hello-netstack",
                    ("192.0.2.2", 9001 if bad == "peer" else 9000))

        def getsockname(self):
            return ("192.0.2.1", 50001)

    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    script = module.udp_echo_script("192.0.2.1", "192.0.2.2").split("\n", 1)[1].rsplit("\nPY", 1)[0]
    if bad:
        with pytest.raises(TimeoutError if bad == "timeout" else AssertionError):
            exec(compile(script, "udp-client", "exec"), {})
    else:
        exec(compile(script, "udp-client", "exec"), {})
        assert b"hello-netstack".hex() in capsys.readouterr().out


@pytest.mark.parametrize("missing", [None, "request", "reply", "udp-request", "udp-reply"])
def test_udp_capture_requires_arp_and_both_udp_directions(missing):
    module = load_module()
    lines = {
        "request": "ARP, Request who-has 192.0.2.2 tell 192.0.2.1, length 28",
        "reply": "ARP, Reply 192.0.2.2 is-at 02:00:00:00:00:02, length 46",
        "udp-request": "192.0.2.1.50001 > 192.0.2.2.9000: UDP, length 13",
        "udp-reply": "192.0.2.2.9000 > 192.0.2.1.50001: [udp sum ok] UDP, length 13",
    }
    text = "PULSAROS_PACKET_COUNT=4\n" + "\n".join(v for k, v in lines.items() if k != missing)
    assertions = module.capture_assertions("192.0.2.1", "192.0.2.2")
    if missing:
        with pytest.raises(pytest.fail.Exception):
            _assert_decoded_capture(text, assertions, "udp")
    else:
        _assert_decoded_capture(text, assertions, "udp")
