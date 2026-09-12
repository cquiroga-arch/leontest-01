import pytest

from vagscan.elm327.driver import ELM327Driver
from vagscan.vag.kwp2000 import KWP2000Client, KWP2000Error
from vagscan.vag.tp20 import TP20Client

from .conftest import LIVE_BUS_TRAFFIC, FakeTransport


def _client_on_channel(responses):
    # Live bus traffic so the safety interlock can confirm the tx ID is free.
    transport = FakeTransport({"ATMA": LIVE_BUS_TRAFFIC, **responses})
    transport.open()
    driver = ELM327Driver(transport)
    tp20 = TP20Client(driver, bus_listen_seconds=0.05)
    channel = tp20.open_channel("01", tx_id=0x300, rx_id=0x310)
    return KWP2000Client(tp20, channel), transport


def test_start_diagnostic_session_positive_response():
    # request: SID 0x10, subfunction 0x89 -> len=02,10,89
    kwp, _ = _client_on_channel({"021089FFFFFFFFFF": "310 02 50 89 FF FF FF FF FF"})
    reply = kwp.start_diagnostic_session(0x89)
    assert reply == bytes([0x89])


def test_clear_diagnostic_information_positive_response():
    # request: SID 0x14, data 0xFF 0xFF -> len=03,14,FF,FF
    kwp, transport = _client_on_channel({"0314FFFFFFFFFFFF": "310 01 54 FF FF FF FF FF"})
    kwp.clear_diagnostic_information()  # should not raise
    assert "021089FFFFFFFFFF" not in transport.written  # sanity: only the clear request was sent


def test_negative_response_raises_with_nrc():
    # ECU rejects the clear request: 7F 14 22 (conditionsNotCorrect)
    kwp, _ = _client_on_channel({"0314FFFFFFFFFFFF": "310 03 7F 14 22 FF FF FF"})
    with pytest.raises(KWP2000Error) as exc_info:
        kwp.clear_diagnostic_information()
    assert exc_info.value.nrc == 0x22


def test_read_dtc_by_status_returns_raw_payload():
    kwp, _ = _client_on_channel({"021800FFFFFFFFFF": "310 03 58 01 33 FF FF FF"})
    payload = kwp.read_dtc_by_status(0x00)
    assert payload == bytes([0x01, 0x33])
