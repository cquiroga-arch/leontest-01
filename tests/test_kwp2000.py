import pytest

from vagscan.elm327.driver import ELM327Driver
from vagscan.vag.kwp2000 import KWP2000Client, KWP2000Error
from vagscan.vag.tp20 import TP20Client

from .conftest import SimBusTransport, sim_bus


def _kwp(canned=None, **engine):
    bus = sim_bus(**engine)
    if canned:
        bus.module(0x01).canned.update(canned)
    transport = SimBusTransport(bus)
    transport.open()
    tp20 = TP20Client(ELM327Driver(transport), bus_listen_seconds=0.05)
    channel = tp20.discover_and_open("01")
    return KWP2000Client(tp20, channel), bus


def test_start_diagnostic_session_positive_response():
    kwp, _ = _kwp()
    assert kwp.start_diagnostic_session(0x89) == bytes([0x89])


def test_clear_diagnostic_information():
    kwp, bus = _kwp()
    kwp.clear_diagnostic_information()
    assert bus.module(0x01).faults_cleared


def test_negative_response_raises_with_nrc():
    kwp, _ = _kwp(canned={bytes([0x14, 0xFF, 0xFF]): bytes([0x7F, 0x14, 0x22])})
    with pytest.raises(KWP2000Error) as exc_info:
        kwp.clear_diagnostic_information()
    assert exc_info.value.nrc == 0x22


def test_read_dtc_by_status_returns_raw_payload():
    kwp, _ = _kwp(canned={bytes([0x18, 0x00]): bytes([0x58, 0x01, 0x33])})
    assert kwp.read_dtc_by_status(0x00) == bytes([0x01, 0x33])


def test_response_pending_is_waited_for():
    kwp, bus = _kwp()
    kwp.start_diagnostic_session(0x89)
    kwp.basic_setting_start(60)  # 31 B9 answers 7F 31 78, then 71 B9
    assert bus.module(0x01).adapt_started is not None


def test_basic_settings_request_bytes():
    kwp, bus = _kwp()
    kwp.start_diagnostic_session(0x89)
    kwp.basic_setting_start(60)
    kwp.basic_setting_stop(60)
    reqs = bus.module(0x01).requests
    assert bytes([0x31, 0xBA, 0x00, 60]) in reqs
    assert bytes([0x31, 0xB9, 0x00, 60]) in reqs
    assert bytes([0x32, 0xB8, 0x00, 60]) in reqs


def test_read_group_decodes_cells():
    kwp, _ = _kwp()
    values = kwp.read_group(0x01)
    assert values[0].unit == "rpm" and values[0].value == pytest.approx(1 * 0xC8 * 0x8C / 5)
