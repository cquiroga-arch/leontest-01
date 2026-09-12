"""Capability probing for the adapter actually plugged in.

The cheap blue "ELM327 mini" clones answer an unimplemented AT command with
`?` rather than saying so, so the advanced features have to ask rather than
assume.
"""
from vagscan.elm327.driver import AdapterCapabilities, ELM327Driver

from .conftest import FakeTransport


def _driver(responses, default="OK"):
    transport = FakeTransport(responses, default=default)
    transport.open()
    return ELM327Driver(transport), transport


def test_full_featured_adapter_reports_everything_supported():
    driver, _ = _driver({})
    caps = driver.probe_capabilities()

    assert caps.monitor_all
    assert caps.can_do_vag_probing
    assert caps.missing() == []


def test_clone_without_monitor_mode_is_detected():
    """The exact case that matters: standard OBD works, but the interlock
    that gates the VAG probe cannot see the bus."""
    driver, _ = _driver({"ATMA": "?"})
    caps = driver.probe_capabilities()

    assert caps.monitor_all is False
    assert caps.can_do_vag_probing is False
    assert caps.can_do_standard_obd is True
    assert "monitor_all" in caps.missing()


def test_clone_missing_several_commands():
    driver, _ = _driver({"ATMA": "?", "ATCAF0": "?", "ATCRA7E8": "?"})
    caps = driver.probe_capabilities()

    assert set(caps.missing()) == {"monitor_all", "can_auto_format_off", "receive_filter"}
    assert caps.can_do_vag_probing is False


def test_a_command_that_errors_counts_as_unsupported_not_a_crash():
    driver, _ = _driver({"ATSH7DF": "BUS ERROR"})
    caps = driver.probe_capabilities()

    assert caps.set_header is False
    assert caps.can_do_vag_probing is False


def test_probe_stops_monitor_mode_after_testing_it():
    """ATMA streams until something interrupts it - leaving it running would
    poison every command after."""
    driver, transport = _driver({})
    driver.probe_capabilities()

    assert "ATMA" in transport.written
    assert transport.written.index("ATMA") < transport.written.index("")


def test_standard_obd_never_depends_on_optional_commands():
    caps = AdapterCapabilities()  # nothing supported at all
    assert caps.can_do_standard_obd is True
