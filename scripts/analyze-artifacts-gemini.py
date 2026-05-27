#!/usr/bin/env python3
"""Summarize testbed artifacts and optionally ask Gemini for triage."""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
LOGS = ROOT / "logs"
PCAPS = ROOT / "pcaps"
JUNIT = ROOT / "junit"
REPORT_MD = ARTIFACTS / "ai-analysis.md"
REPORT_JSON = ARTIFACTS / "ai-analysis-input.json"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_MAX_OUTPUT_TOKENS = 4096
MAX_LOG_CHARS = 4000


def read_text(path: pathlib.Path, limit: int = MAX_LOG_CHARS) -> str:
    try:
        data = path.read_text(errors="replace")
    except FileNotFoundError:
        return ""
    if len(data) <= limit:
        return data
    return data[-limit:]


def read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def redact(text: str) -> str:
    # Keep this conservative: strip obvious private key blocks and common token lines.
    text = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"(?i)(token|password|secret|api[_-]?key)=\S+", r"\1=[REDACTED]", text)
    return text


def parse_junit_file(path: pathlib.Path) -> dict:
    result = {
        "file": str(path.relative_to(ROOT)),
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "failed_cases": [],
    }
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        result["parse_error"] = str(exc)
        return result

    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    for suite in suites:
        result["tests"] += int(suite.attrib.get("tests", 0))
        result["failures"] += int(suite.attrib.get("failures", 0))
        result["errors"] += int(suite.attrib.get("errors", 0))
        result["skipped"] += int(suite.attrib.get("skipped", 0))

    for case in root.iter("testcase"):
        failure = case.find("failure")
        error = case.find("error")
        node = failure if failure is not None else error
        if node is not None:
            result["failed_cases"].append(
                {
                    "classname": case.attrib.get("classname", ""),
                    "name": case.attrib.get("name", ""),
                    "message": node.attrib.get("message", ""),
                    "text": redact((node.text or "")[-2000:]),
                }
            )
    return result


def topology_summary(topology: dict) -> dict:
    if not topology:
        return {}

    hosts = []
    for host_name, host in sorted(topology.get("hosts", {}).items()):
        hosts.append(
            {
                "name": host_name,
                "groups": host.get("groups", []),
                "management_ip": host.get("management_ip", ""),
                "nics": [
                    {
                        "name": nic.get("name", ""),
                        "network": nic.get("network", ""),
                        "bridge": nic.get("bridge", ""),
                        "management": bool(nic.get("management", False)),
                    }
                    for nic in host.get("nics", [])
                ],
            }
        )

    networks = {}
    for name, network in sorted(topology.get("networks", {}).items()):
        networks[name] = {
            key: network.get(key, "")
            for key in ("mode", "vnet", "inner_vlan", "bridge")
            if key in network
        }

    segments = {}
    for name, segment in sorted(topology.get("segments", {}).items()):
        segments[name] = {
            key: segment.get(key)
            for key in ("vni", "vlan")
            if key in segment
        }
        segments[name]["vteps"] = [
            {
                "host": vtep.get("host", ""),
                "underlay_nic": vtep.get("underlay_nic", ""),
                "underlay_ip": vtep.get("underlay_ip", ""),
                "local_nics": vtep.get("local_nics", []),
            }
            for vtep in segment.get("vteps", [])
        ]
        segments[name]["members"] = [
            {
                key: member.get(key)
                for key in ("host", "nic", "mode", "vlan", "ip")
                if key in member
            }
            for member in segment.get("members", [])
        ]

    checks = []
    for check in topology.get("checks", []):
        item = {
            key: check.get(key)
            for key in ("name", "type", "segment", "source", "destination")
            if key in check
        }
        if "trigger" in check:
            item["trigger"] = {
                key: check["trigger"].get(key)
                for key in ("type", "source", "destination")
                if key in check["trigger"]
            }
        if "captures" in check:
            item["captures"] = [
                {
                    key: capture.get(key)
                    for key in ("host", "nic", "filter")
                    if key in capture
                }
                for capture in check["captures"]
            ]
        checks.append(item)

    return {
        "name": topology.get("name", ""),
        "description": topology.get("description", ""),
        "network_mode": topology.get("network_mode", ""),
        "hosts": hosts,
        "networks": networks,
        "segments": segments,
        "checks": checks,
    }


def collect_inputs() -> dict:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    topology = topology_summary(read_json(ARTIFACTS / "topology.json"))
    run_state = read_json(ARTIFACTS / "run-state.json")
    junit = [parse_junit_file(path) for path in sorted(JUNIT.glob("*.xml"))]
    log_samples = {}
    for path in sorted(LOGS.glob("*.log")):
        name = str(path.relative_to(ROOT))
        if any(marker in path.name for marker in ("dmesg", "journal", "ip-link", "ip-addr", "uname")):
            log_samples[name] = redact(read_text(path))

    existing_artifacts = {}
    for path in sorted(ARTIFACTS.glob("*")):
        if path.name in {REPORT_MD.name, REPORT_JSON.name} or not path.is_file():
            continue
        if path.suffix in {".env", ".txt", ".log", ".md"}:
            existing_artifacts[str(path.relative_to(ROOT))] = redact(read_text(path, limit=12000))

    pcaps = []
    for path in sorted(PCAPS.glob("*.pcap")):
        pcaps.append({"file": str(path.relative_to(ROOT)), "bytes": path.stat().st_size})

    return {
        "run": {
            "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "scenario": os.environ.get("SCENARIO", ""),
            "runner_name": os.environ.get("RUNNER_NAME", ""),
        },
        "topology": topology,
        "run_state": run_state,
        "topology_env": redact(read_text(ARTIFACTS / "topology.env", limit=12000)),
        "artifact_text": existing_artifacts,
        "junit": junit,
        "pcaps": pcaps,
        "log_samples": log_samples,
    }


def local_summary(data: dict) -> str:
    lines = ["# AI Artifact Analysis", "", "## Local Summary", ""]
    run = data["run"]
    lines.append(f"- Scenario: `{run.get('scenario') or 'unknown'}`")
    lines.append(f"- GitHub run: `{run.get('github_run_id') or 'local'}`")
    topology = data.get("topology", {})
    if topology:
        lines.append(f"- Topology: `{topology.get('name') or 'unknown'}`")
    run_state = data.get("run_state", {})
    if run_state:
        lines.append(f"- Last lifecycle phase: `{run_state.get('phase') or 'unknown'}`")

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failed_cases = []
    for item in data["junit"]:
        for key in totals:
            totals[key] += item.get(key, 0)
        failed_cases.extend(item.get("failed_cases", []))

    lines.append(
        f"- JUnit: {totals['tests']} tests, {totals['failures']} failures, "
        f"{totals['errors']} errors, {totals['skipped']} skipped"
    )
    lines.append(f"- PCAP files: {len(data['pcaps'])}")
    for pcap in data["pcaps"]:
        lines.append(f"  - `{pcap['file']}`: {pcap['bytes']} bytes")

    if failed_cases:
        lines.extend(["", "## Failed Cases", ""])
        for case in failed_cases:
            lines.append(f"- `{case['classname']}::{case['name']}`: {case['message']}")
    else:
        lines.extend(["", "No JUnit failures were found."])

    return "\n".join(lines) + "\n"


def gemini_prompt(data: dict) -> str:
    return (
        "You are analyzing artifacts from a disposable Proxmox CI testbed for "
        "Linux VXLAN and kernel smoke tests. Identify the likely root cause, "
        "the strongest evidence, missing evidence, and concrete next debugging "
        "steps. Be concise and do not invent facts not present in the artifact "
        "summary.\n\n"
        "Use the `topology` object derived from artifacts/topology.json as the "
        "authoritative expected topology when it is present. Do not compare the "
        "run to any fixed two-node or two-VTEP reference layout unless that is "
        "the topology named in the artifact summary. Treat segment member IPs, "
        "VLANs, VTEPs, local NICs, and checks from that topology object as "
        "intentional configuration. If topology data is absent, avoid "
        "topology-specific misconfiguration claims and ask for the missing "
        "topology artifact instead. If JUnit reports zero failures and pcap "
        "files are present and non-empty, state that the run appears successful "
        "and only list low-confidence observations under a separate caveats "
        "section. Keep the response under 800 words and end with a complete "
        "sentence.\n\n"
        f"Artifact summary JSON:\n{json.dumps(data, indent=2)[:50000]}"
    )


def call_gemini(data: dict) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "Gemini analysis skipped: `GEMINI_API_KEY` is not set.\n"

    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    max_output_tokens = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": gemini_prompt(data)}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": max_output_tokens,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return f"Gemini analysis failed: HTTP {exc.code}\n\n```json\n{detail[:4000]}\n```\n"
    except Exception as exc:  # noqa: BLE001 - report fail-soft analyzer errors.
        return f"Gemini analysis failed: {type(exc).__name__}: {exc}\n"

    candidate = payload.get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts if part.get("text"))
    if not text.strip():
        return "Gemini analysis returned no text.\n"

    finish_reason = candidate.get("finishReason", "")
    if finish_reason == "MAX_TOKENS":
        text += (
            "\n\n> Warning: Gemini stopped because it reached "
            f"`GEMINI_MAX_OUTPUT_TOKENS={max_output_tokens}`. Increase that "
            "workflow variable if the analysis is still truncated."
        )
    return text.strip() + "\n"


def write_step_summary(report: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with pathlib.Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(report)
        if not report.endswith("\n"):
            handle.write("\n")


def main() -> int:
    data = collect_inputs()
    REPORT_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    report = local_summary(data)
    report += "\n## Gemini Analysis\n\n"
    report += call_gemini(data)
    REPORT_MD.write_text(report, encoding="utf-8")
    write_step_summary(report)
    print(f"Wrote {REPORT_MD.relative_to(ROOT)}")
    print(f"Wrote {REPORT_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
