import pytest

from vagscan.elm327.driver import ELM327Driver, ELM327Error, ELM327Timeout
from vagscan.elm327.protocols import OBDProtocol

from .conftest import FakeTransport


def test_send_command_returns_stripped_reply():
    transport = FakeTransport({"ATI": "ELM327 v1.5"})
    transport.open()
    driver = ELM327Driver(transport)
    assert driver.send_command("ATI") == "ELM327 v1.5"


def test_send_command_raises_on_no_data():
    transport = FakeTransport({"01AC": "NO DATA"})
    transport.open()
    driver = ELM327Driver(transport)
    with pytest.raises(ELM327Error):
        driver.send_command("01AC")


def test_send_command_raises_on_unrecognized():
    transport = FakeTransport({"BOGUS": "?"})
    transport.open()
    driver = ELM327Driver(transport)
    with pytest.raises(ELM327Error):
        driver.send_command("BOGUS")


def test_send_command_times_out_on_empty_reply():
    transport = FakeTransport(default="")
    transport.open()
    driver = ELM327Driver(transport)
    with pytest.raises(ELM327Timeout):
        driver.send_command("AT WHATEVER")


def test_initialize_runs_expected_sequence_and_opens_closed_transport():
    transport = FakeTransport()
    driver = ELM327Driver(transport)  # transport not opened yet
    driver.initialize(protocol=OBDProtocol.AUTO)
    assert transport.is_open
    assert "ATE0" in transport.written
    assert "ATL0" in transport.written
    assert "ATS0" in transport.written
    assert "ATH1" in transport.written
    assert "ATSP0" in transport.written
    assert driver.headers_on is True


def test_reconnect_callback_reruns_initialize():
    transport = FakeTransport()
    driver = ELM327Driver(transport)
    driver.initialize()
    transport.written.clear()
    transport._reconnect_cb(transport)
    assert "ATSP0" in transport.written  # initialize() ran again


def test_identify_collects_fingerprint_fields():
    transport = FakeTransport(
        {
            "ATI": "ELM327 v1.5",
            "AT@1": "OBDLink SX",
            "AT@2": "MyDongle",
            "ATRV": "12.6V",
        }
    )
    transport.open()
    driver = ELM327Driver(transport)
    fp = driver.identify()
    assert fp.ati == "ELM327 v1.5"
    assert fp.at_device_desc == "OBDLink SX"
    assert fp.at_device_id == "MyDongle"
    assert fp.voltage == "12.6V"


def test_detected_protocol_parses_dpn():
    transport = FakeTransport({"ATDPN": "A6"})
    transport.open()
    driver = ELM327Driver(transport)
    assert driver.detected_protocol() == OBDProtocol.ISO_15765_4_CAN_11BIT_500K
