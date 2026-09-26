"""Regression tests for issues found on the real Seat Leon (v0.1.2 field run):
the battery gauge reading n/d, a "SEARCHING..." reply derailing the VIN, a
late reply desyncing the next request, and an unsupported mode (7F) shown as
an error.
"""
from vagscan.elm327.driver import ELM327Driver
from vagscan.obd2.service import OBD2Service

from .conftest import FakeTransport


def _service(responses, default="OK"):
    transport = FakeTransport(responses, default=default)
    transport.open()
    return OBD2Service(ELM327Driver(transport)), transport


def test_battery_voltage_read_from_atrv():
    transport = FakeTransport({"ATRV": "12.4V"})
    transport.open()
    driver = ELM327Driver(transport)
    assert driver.read_battery_voltage() == 12.4


def test_battery_voltage_tolerates_no_v_suffix_and_spaces():
    transport = FakeTransport({"ATRV": " 13.9 "})
    transport.open()
    assert ELM327Driver(transport).read_battery_voltage() == 13.9


def test_battery_voltage_none_when_unparseable():
    transport = FakeTransport({"ATRV": "?"})
    transport.open()
    assert ELM327Driver(transport).read_battery_voltage() is None


def test_searching_reply_is_not_treated_as_data():
    """The car answers "SEARCHING..." while negotiating a protocol; that must
    not be parsed as a VIN or leave the exchange desynced."""
    service, _ = _service({"0902": "SEARCHING..."})
    assert service.read_vin() is None


def test_negative_response_permanent_dtc_is_empty_not_error():
    """7F 0A 11 = ECU doesn't support permanent DTCs (real on this car).
    Must read as "none", not blow up with 'no mode-ack 4A'."""
    service, _ = _service({"0A": "83 F1 10 7F 0A 11 1E"})
    assert service.read_permanent_dtcs() == []


def test_negative_response_stored_dtc_is_empty_not_error():
    service, _ = _service({"03": "7F 03 11"})
    assert service.read_stored_dtcs() == []


def test_input_is_flushed_before_each_command():
    """Guards the desync fix: every command must clear stale input first, so
    a late reply from the previous request can't be read as this one's."""
    transport = FakeTransport({"0100": "41 00 BE 1F B8 10"})
    transport.open()
    flushes = {"n": 0}
    original = transport.reset_input

    def counting():
        flushes["n"] += 1
        original()

    transport.reset_input = counting
    ELM327Driver(transport).send_command("0100")
    assert flushes["n"] == 1


def test_dtc_read_still_works_with_headers_in_reply():
    # K-line style reply with header bytes 82 F1 10 before the 43 ack.
    service, _ = _service({"03": "82 F1 10 43 01 33 CE"})
    codes = [d.code for d in service.read_stored_dtcs()]
    assert codes == ["P0133"]
