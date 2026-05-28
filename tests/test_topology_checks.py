import json
import ipaddress
import pathlib
import re
import shlex
import subprocess
import time

import pytest

from conftest import PCAPS, TOPOLOGY_JSON, host_ip, host_nic, iface_by_mac, scp_from, ssh

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
LOGS = ROOT / "logs"
PERF_METRICS = ARTIFACTS / "perf-metrics.json"


def _load_checks_for_collection():
    if not TOPOLOGY_JSON.exists():
        return []
    data = json.loads(TOPOLOGY_JSON.read_text(encoding="utf-8"))
    return data.get("checks", [])


def pytest_generate_tests(metafunc):
    if "topology_check" not in metafunc.fixturenames:
        return
    checks = _load_checks_for_collection()
    if checks:
        metafunc.parametrize(
            "topology_check",
            checks,
            ids=[check.get("name", f"check-{index}") for index, check in enumerate(checks)],
        )
    else:
        metafunc.parametrize("topology_check", [None], ids=["no-topology-checks"])


def _check_value(check, name, default):
    return int(check.get(name, default))


def _run_ping_check(topology, ssh_user, ssh_key, check):
    count = _check_value(check, "count", 3)
    wait = _check_value(check, "wait", 2)
    command = f"ping -c {count} -W {wait} {shlex.quote(str(check['destination']))}"
    return ssh(
        topology,
        ssh_user,
        ssh_key,
        check["source"],
        command,
        timeout=_check_value(check, "timeout", 60),
    )


def _address(cidr):
    return str(ipaddress.ip_interface(str(cidr)).ip)


def _segment_members(segment):
    return {
        f"{member['host']}.{member['nic']}": member
        for member in segment.get("members", [])
    }


def _check_pairs(segment, check):
    members = _segment_members(segment)
    if check.get("pairs"):
        return [
            (members[pair["source"]], members[pair["destination"]])
            for pair in check["pairs"]
        ]
    return [
        (source, destination)
        for source in segment.get("members", [])
        for destination in segment.get("members", [])
        if source["host"] != destination["host"] or source["nic"] != destination["nic"]
    ]


def _run_segment_ping_matrix_check(topology, ssh_user, ssh_key, check):
    segment = topology["__resolved__"]["segments"][check["segment"]]
    pairs = _check_pairs(segment, check)

    count = _check_value(check, "count", 3)
    wait = _check_value(check, "wait", 2)
    timeout = _check_value(check, "timeout", 60)
    for source, destination in pairs:
        ping_check = {
            "source": source["host"],
            "destination": _address(destination["ip"]),
            "count": count,
            "wait": wait,
            "timeout": timeout,
        }
        _run_ping_check(topology, ssh_user, ssh_key, ping_check)


def _parse_ping_metrics(output):
    metrics = {}
    packet_match = re.search(
        r"(?P<tx>\d+)\s+packets transmitted,\s+"
        r"(?P<rx>\d+)\s+(?:packets\s+)?received,.*?"
        r"(?P<loss>[0-9.]+)%\s+packet loss",
        output,
        re.DOTALL,
    )
    if packet_match:
        metrics.update(
            {
                "packets_transmitted": int(packet_match.group("tx")),
                "packets_received": int(packet_match.group("rx")),
                "packet_loss_percent": float(packet_match.group("loss")),
            }
        )
    rtt_match = re.search(
        r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
        r"(?P<min>[0-9.]+)/(?P<avg>[0-9.]+)/(?P<max>[0-9.]+)/(?P<mdev>[0-9.]+) ms",
        output,
    )
    if rtt_match:
        metrics.update(
            {
                "rtt_min_ms": float(rtt_match.group("min")),
                "rtt_avg_ms": float(rtt_match.group("avg")),
                "rtt_max_ms": float(rtt_match.group("max")),
                "rtt_mdev_ms": float(rtt_match.group("mdev")),
            }
        )
    return metrics


def _parse_pktgen_metrics(output):
    metrics = {}
    for key, patterns in {
        "tx_packets": (r"\btx\s+pkts?\s*[:=]\s*([0-9,]+)", r"\bopackets\s*[:=]\s*([0-9,]+)"),
        "rx_packets": (r"\brx\s+pkts?\s*[:=]\s*([0-9,]+)", r"\bipackets\s*[:=]\s*([0-9,]+)"),
        "tx_pps": (r"\btx\s+pps\s*[:=]\s*([0-9,.]+)", r"\bo?pps\s*[:=]\s*([0-9,.]+)"),
        "tx_bytes": (r"\bobytes\s*[:=]\s*([0-9,]+)",),
        "rx_bytes": (r"\bibytes\s*[:=]\s*([0-9,]+)",),
    }.items():
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                value = match.group(1).replace(",", "")
                metrics[key] = float(value) if "." in value else int(value)
                break
    return metrics


def _metric_warnings(metrics, thresholds):
    warnings = []
    checks = {
        "max_loss_percent": "packet_loss_percent",
        "max_rtt_avg_ms": "rtt_avg_ms",
        "max_rtt_max_ms": "rtt_max_ms",
    }
    for threshold_key, metric_key in checks.items():
        if threshold_key not in thresholds or metric_key not in metrics:
            continue
        threshold = float(thresholds[threshold_key])
        if float(metrics[metric_key]) > threshold:
            warnings.append(
                f"{metric_key}={metrics[metric_key]} exceeds {threshold_key}={threshold}"
            )
    return warnings


def _append_perf_metric(metric):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(PERF_METRICS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"probes": []}
    data.setdefault("probes", []).append(metric)
    PERF_METRICS.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_segment_perf_probe_check(topology, ssh_user, ssh_key, check):
    segment = topology["__resolved__"]["segments"][check["segment"]]
    pairs = _check_pairs(segment, check)
    count = _check_value(check, "count", 10)
    wait = _check_value(check, "wait", 2)
    timeout = _check_value(check, "timeout", 60)
    thresholds = check.get("thresholds", {})

    for source, destination in pairs:
        destination_ip = _address(destination["ip"])
        result = ssh(
            topology,
            ssh_user,
            ssh_key,
            source["host"],
            f"ping -c {count} -W {wait} {shlex.quote(destination_ip)}",
            timeout=timeout,
            check=False,
        )
        if result.returncode == 255:
            pytest.fail(f"{check['name']}: ssh failed on {source['host']}:\n{result.stderr}")
        ping_output = result.stdout + result.stderr
        ping_metrics = _parse_ping_metrics(ping_output)
        warnings = _metric_warnings(ping_metrics, thresholds)
        metric = {
            "name": check["name"],
            "segment": check["segment"],
            "source": f"{source['host']}.{source['nic']}",
            "destination": f"{destination['host']}.{destination['nic']}",
            "destination_ip": destination_ip,
            "report_only": True,
            "ping_returncode": result.returncode,
            "ping": ping_metrics,
            "warnings": warnings,
        }

        if check.get("pktgen", False):
            pktgen_name = re.sub(
                r"[^A-Za-z0-9_.-]",
                "-",
                f"{check['name']}-{source['host']}-{destination['host']}-pktgen",
            )
            pktgen_check = {
                "name": pktgen_name,
                "source": source["host"],
                "nic": source["nic"],
                "destination_mac": destination["mac"],
                "source_ip": _address(source["ip"]),
                "destination_ip": destination_ip,
                "protocol": check.get("protocol", "udp"),
                "packet_size": _check_value(check, "packet_size", 128),
                "rate_percent": _check_value(check, "rate_percent", 1),
                "duration": _check_value(check, "duration", 5),
                "timeout": _check_value(check, "pktgen_timeout", 30),
            }
            pktgen_result = _run_pktgen_dpdk_check(
                topology,
                ssh_user,
                ssh_key,
                pktgen_check,
                check_result=False,
            )
            LOGS.mkdir(parents=True, exist_ok=True)
            local_log = LOGS / f"{pktgen_name}.log"
            local_log.write_text(pktgen_result.stdout + pktgen_result.stderr, encoding="utf-8")
            metric["pktgen"] = {
                "returncode": pktgen_result.returncode,
                "log": str(local_log.relative_to(ROOT)),
                "metrics": _parse_pktgen_metrics(pktgen_result.stdout + pktgen_result.stderr),
            }
            if pktgen_result.returncode != 0:
                metric["warnings"].append(f"pktgen exited with rc={pktgen_result.returncode}")

        _append_perf_metric(metric)
        if metric["warnings"]:
            print(
                f"{check['name']} report-only warnings for "
                f"{metric['source']} -> {metric['destination']}: {metric['warnings']}"
            )


def _pcap_name(check_name, capture):
    raw = f"{check_name}-{capture['host']}-{capture['nic']}.pcap"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", raw)


def _decode_name(check_name, capture):
    raw = f"{check_name}-{capture['host']}-{capture['nic']}-tcpdump.log"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", raw)


def _start_capture(topology, ssh_user, ssh_key, check_name, capture):
    nic = host_nic(topology, capture["host"], capture["nic"])
    capture_if = iface_by_mac(topology, ssh_user, ssh_key, capture["host"], nic["mac"])
    remote_pcap = f"/tmp/pulsaros-testbed/{_pcap_name(check_name, capture)}"
    local_pcap = PCAPS / _pcap_name(check_name, capture)
    remote_decode = f"/tmp/pulsaros-testbed/{_decode_name(check_name, capture)}"
    local_decode = LOGS / _decode_name(check_name, capture)

    ssh(
        topology,
        ssh_user,
        ssh_key,
        capture["host"],
        f"sudo -n mkdir -p /tmp/pulsaros-testbed && sudo -n rm -f {remote_pcap}",
    )
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
        f"{ssh_user}@{host_ip(topology, capture['host'])}",
        "sudo -n sh -c "
        + shlex.quote(
            f"timeout 20 tcpdump -U -i {capture_if} -w {remote_pcap} {capture['filter']}"
        ),
    ]
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {
        "host": capture["host"],
        "remote_pcap": remote_pcap,
        "remote_decode": remote_decode,
        "local_pcap": local_pcap,
        "local_decode": local_decode,
        "process": process,
    }


def _stop_capture(capture):
    process = capture["process"]
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _collect_capture(topology, ssh_user, ssh_key, capture):
    ssh(
        topology,
        ssh_user,
        ssh_key,
        capture["host"],
        f"sudo -n chmod 0644 {capture['remote_pcap']} && test -s {capture['remote_pcap']}",
        timeout=30,
    )
    result = scp_from(
        topology,
        ssh_user,
        ssh_key,
        capture["host"],
        capture["remote_pcap"],
        capture["local_pcap"],
    )
    assert result.returncode == 0, result.stderr
    assert pathlib.Path(capture["local_pcap"]).stat().st_size > 0
    decode_script = (
        f"count=$(tcpdump -nn -r {shlex.quote(capture['remote_pcap'])} 2>/dev/null | wc -l); "
        f"printf 'PULSAROS_PACKET_COUNT=%s\\n' \"$count\" > {shlex.quote(capture['remote_decode'])}; "
        f"tcpdump -nn -e -vv -r {shlex.quote(capture['remote_pcap'])} "
        f">> {shlex.quote(capture['remote_decode'])} 2>&1; "
        f"chmod 0644 {shlex.quote(capture['remote_decode'])}"
    )
    decode_command = f"sudo -n sh -c {shlex.quote(decode_script)}"
    ssh(topology, ssh_user, ssh_key, capture["host"], decode_command, timeout=30)
    result = scp_from(
        topology,
        ssh_user,
        ssh_key,
        capture["host"],
        capture["remote_decode"],
        capture["local_decode"],
    )
    assert result.returncode == 0, result.stderr
    assert pathlib.Path(capture["local_decode"]).stat().st_size > 0


def _packet_count(decoded_text):
    return sum(
        int(match.group(1))
        for match in re.finditer(r"^PULSAROS_PACKET_COUNT=(\d+)$", decoded_text, re.MULTILINE)
    )


def _assert_decoded_capture(decoded_text, assertions, label):
    if not assertions:
        return
    min_packets = int(assertions.get("min_packets", 0))
    if min_packets and _packet_count(decoded_text) < min_packets:
        pytest.fail(
            f"{label}: expected at least {min_packets} decoded packets, "
            f"got {_packet_count(decoded_text)}"
        )

    required = list(assertions.get("contains", []))
    forbidden = list(assertions.get("not_contains", []))
    if assertions.get("vxlan_vni") not in (None, ""):
        required.append(rf"\b(vni|VXLAN).*{re.escape(str(assertions['vxlan_vni']))}\b")
    for ip in assertions.get("inner_ips", []):
        required.append(re.escape(str(ip)))
    for ip in assertions.get("outer_ips", []):
        required.append(re.escape(str(ip)))

    missing = [pattern for pattern in required if not re.search(pattern, decoded_text, re.MULTILINE)]
    present_forbidden = [
        pattern for pattern in forbidden if re.search(pattern, decoded_text, re.MULTILINE)
    ]
    if missing or present_forbidden:
        detail = []
        if missing:
            detail.append(f"missing required patterns: {missing}")
        if present_forbidden:
            detail.append(f"forbidden patterns present: {present_forbidden}")
        pytest.fail(f"{label}: " + "; ".join(detail))


def _assert_captures(captures, assertions):
    decoded_text = "\n".join(
        pathlib.Path(capture["local_decode"]).read_text(errors="replace")
        for capture in captures
    )
    label = ", ".join(f"{capture['host']}:{capture['local_decode'].name}" for capture in captures)
    _assert_decoded_capture(decoded_text, assertions, label)


def _run_packet_capture_check(topology, ssh_user, ssh_key, check):
    captures = []
    try:
        captures = [
            _start_capture(topology, ssh_user, ssh_key, check["name"], capture)
            for capture in check["captures"]
        ]
        time.sleep(_check_value(check, "settle", 2))
        _run_ping_check(topology, ssh_user, ssh_key, check["trigger"])
        time.sleep(_check_value(check, "post_trigger_wait", 3))
    finally:
        for capture in captures:
            _stop_capture(capture)

    for capture in captures:
        _collect_capture(topology, ssh_user, ssh_key, capture)
    _assert_captures(captures, check.get("assertions", {}))


def _segment_capture_assertions(segment, pairs, check):
    assertions = dict(check.get("assertions", {}))
    assertions.setdefault("min_packets", 1)
    assertions.setdefault("vxlan_vni", segment["vni"])
    inner_ips = set(assertions.get("inner_ips", []))
    for source, destination in pairs:
        inner_ips.add(_address(source["ip"]))
        inner_ips.add(_address(destination["ip"]))
    assertions["inner_ips"] = sorted(inner_ips)
    outer_ips = set(assertions.get("outer_ips", []))
    for vtep in segment.get("vteps", []):
        outer_ips.add(vtep["underlay_address"])
    assertions["outer_ips"] = sorted(outer_ips)
    return assertions


def _run_segment_bidirectional_capture_check(topology, ssh_user, ssh_key, check):
    segment = topology["__resolved__"]["segments"][check["segment"]]
    captures = []
    pairs = _check_pairs(segment, check)
    assertions = _segment_capture_assertions(segment, pairs, check)
    try:
        captures = [
            _start_capture(topology, ssh_user, ssh_key, check["name"], capture)
            for capture in check["captures"]
        ]
        time.sleep(_check_value(check, "settle", 2))
        for source, destination in pairs:
            ping_check = {
                "source": source["host"],
                "destination": _address(destination["ip"]),
                "count": _check_value(check, "count", 3),
                "wait": _check_value(check, "wait", 2),
                "timeout": _check_value(check, "timeout", 60),
            }
            _run_ping_check(topology, ssh_user, ssh_key, ping_check)
        time.sleep(_check_value(check, "post_trigger_wait", 3))
    finally:
        for capture in captures:
            _stop_capture(capture)

    for capture in captures:
        _collect_capture(topology, ssh_user, ssh_key, capture)
    _assert_captures(captures, assertions)


def _lua_string(value):
    return json.dumps(str(value))


def _run_pktgen_dpdk_check(topology, ssh_user, ssh_key, check, check_result=True):
    nic = host_nic(topology, check["source"], check["nic"])
    source_if = iface_by_mac(topology, ssh_user, ssh_key, check["source"], nic["mac"])
    duration_ms = _check_value(check, "duration", 5) * 1000
    timeout = _check_value(check, "timeout", 30)
    packet_count = _check_value(check, "count", 1024)
    packet_size = _check_value(check, "packet_size", 128)
    rate_percent = _check_value(check, "rate_percent", 1)
    sport = _check_value(check, "source_port", 1234)
    dport = _check_value(check, "destination_port", 5678)
    protocol = check.get("protocol", "udp")
    remote_dir = "/tmp/pulsaros-testbed"
    script_path = f"{remote_dir}/{check['name']}.lua"
    log_path = f"{remote_dir}/{check['name']}.log"
    lua = f"""
package.path = package.path ..";/usr/local/share/pulsaros-pktgen/?.lua;?.lua;test/?.lua;app/?.lua;"
require "Pktgen"
pktgen.screen("off")
pktgen.set("all", "count", {packet_count})
pktgen.set("all", "rate", {rate_percent})
pktgen.set("all", "size", {packet_size})
pktgen.set("all", "sport", {sport})
pktgen.set("all", "dport", {dport})
pktgen.set_mac("all", "src", {_lua_string(nic["mac"])})
pktgen.set_mac("all", "dst", {_lua_string(check["destination_mac"])})
pktgen.set_ipaddr("all", "src", {_lua_string(check["source_ip"])})
pktgen.set_ipaddr("all", "dst", {_lua_string(check["destination_ip"])})
pktgen.set_proto("all", {_lua_string(protocol)})
pktgen.start("all")
pktgen.delay({duration_ms})
pktgen.stop("all")
prints("pktStats", pktgen.pktStats("all"))
pktgen.quit()
"""
    command = (
        "set -euo pipefail; "
        f"mkdir -p {shlex.quote(remote_dir)}; "
        "pktgen_bin=$(command -v pktgen-dpdk || command -v pktgen || true); "
        'if [ -z "$pktgen_bin" ]; then echo "pktgen-dpdk/pktgen not found" >&2; exit 127; fi; '
        f"cat > {shlex.quote(script_path)} <<'PULSAROS_PKTGEN_LUA'\n"
        f"{lua}"
        "PULSAROS_PKTGEN_LUA\n"
        "lcore_count=$(nproc); "
        'if [ "$lcore_count" -lt 2 ]; then '
        'echo "pktgen-dpdk requires at least 2 lcores, found ${lcore_count}; '
        'check guest CPU count and kernel CONFIG_NR_CPUS" >&2; '
        "exit 1; "
        "fi; "
        'if [ "$lcore_count" -ge 3 ]; then '
        "pktgen_lcores=0,1,2; "
        "pktgen_matrix='[1:2].0'; "
        "else "
        "pktgen_lcores=0,1; "
        "pktgen_matrix=1.0; "
        "fi; "
        "set +e; "
        f"timeout {timeout} \"$pktgen_bin\" -l \"$pktgen_lcores\" -n 2 --no-pci "
        f"--vdev=net_af_packet0,iface={shlex.quote(source_if)} -- "
        f"-P -m \"$pktgen_matrix\" -f {shlex.quote(script_path)} "
        f"> {shlex.quote(log_path)} 2>&1; "
        "pktgen_rc=$?; "
        f"cat {shlex.quote(log_path)}; "
        'if [ "$pktgen_rc" -ne 0 ]; then '
        'echo "=== pktgen failure diagnostics ==="; '
        "trap_line=$(dmesg | grep 'pktgen.*trap' | tail -n 1 || true); "
        'echo "${trap_line}"; '
        "offset=$(printf '%s' \"$trap_line\" | sed -n 's/.*in pktgen\\[\\([^,]*\\),.*/0x\\1/p'); "
        'if [ -n "$offset" ] && command -v addr2line >/dev/null 2>&1; then '
        'addr2line -Cfpe "$pktgen_bin" "$offset" || true; '
        "fi; "
        "fi; "
        "exit ${pktgen_rc}"
    )
    return ssh(
        topology,
        ssh_user,
        ssh_key,
        check["source"],
        f"sudo -n bash -lc {shlex.quote(command)}",
        timeout=timeout + 10,
        check=check_result,
    )


def test_topology_check(topology, ssh_user, ssh_key, topology_check):
    if topology_check is None:
        pytest.skip("topology declares no checks")

    if topology_check["type"] == "ping":
        _run_ping_check(topology, ssh_user, ssh_key, topology_check)
    elif topology_check["type"] == "packet_capture":
        _run_packet_capture_check(topology, ssh_user, ssh_key, topology_check)
    elif topology_check["type"] == "pktgen_dpdk":
        _run_pktgen_dpdk_check(topology, ssh_user, ssh_key, topology_check)
    elif topology_check["type"] == "segment_ping_matrix":
        _run_segment_ping_matrix_check(topology, ssh_user, ssh_key, topology_check)
    elif topology_check["type"] == "segment_bidirectional_capture":
        _run_segment_bidirectional_capture_check(topology, ssh_user, ssh_key, topology_check)
    elif topology_check["type"] == "segment_perf_probe":
        _run_segment_perf_probe_check(topology, ssh_user, ssh_key, topology_check)
    else:
        pytest.fail(f"unsupported topology check type: {topology_check['type']}")
