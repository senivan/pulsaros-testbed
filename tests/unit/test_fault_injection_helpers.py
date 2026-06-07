import importlib.util
import pathlib
from types import SimpleNamespace

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "test_fault_injection.py"


spec = importlib.util.spec_from_file_location("fault_injection", MODULE_PATH)
fault_injection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fault_injection)


def result(returncode):
    return SimpleNamespace(returncode=returncode, stdout="stdout", stderr="stderr")


def test_assert_fault_impact_accepts_matching_expectation():
    fault_injection._assert_fault_impact(result(1), "expected-outage", "outage")
    fault_injection._assert_fault_impact(result(0), "expected-no-outage", "no_outage")


@pytest.mark.parametrize(
    ("returncode", "expected_impact"),
    [
        (0, "outage"),
        (1, "no_outage"),
    ],
)
def test_assert_fault_impact_rejects_mismatched_expectation(returncode, expected_impact):
    with pytest.raises(pytest.fail.Exception):
        fault_injection._assert_fault_impact(result(returncode), "fault", expected_impact)


def test_assert_fault_impact_rejects_ssh_failure():
    with pytest.raises(pytest.fail.Exception):
        fault_injection._assert_fault_impact(result(255), "fault", "outage")
