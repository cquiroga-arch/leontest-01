"""The guided VAG module-clear flow (e.g. airbag/module 15) for a
monitor-capable adapter: parse the channel-setup reply, open the channel,
and clear. Clearing a stored code after a repair is a normal scan-tool
operation - it is not masking (an active fault sets itself again).
"""
import pytest

from vagscan.app.session import VagscanSession
from vagscan.dtc_db import DtcDatabase
from vagscan.elm327.driver import ELM327Driver
from vagscan.obd2 import OBD2Service
from vagscan.vag.bus_logger import BusLogger, parse_raw_frame
from vagscan.vag.tp20 import BusSafetyError, TP20Client, parse_channel_setup

from .conftest import LIVE_BUS_TRAFFIC, FakeTransport


def test_parse_channel_setup_extracts_tx_and_rx():
    frame = parse_raw_frame("215 00 D0 00 03 40 05 00 03")
    tx, rx = parse_channel_setup([frame])
    assert tx == 0x300  # little-endian 00 03
    assert rx == 0x215  # the id the reply arrived on


def test_parse_channel_setup_none_without_ack():
    frame = parse_raw_frame("280 11 22 33 44 55 66 77 88")
    assert parse_channel_setup([frame]) is None


def test_parse_channel_setup_none_on_empty():
    assert parse_channel_setup([]) is None


def _session(responses):
    transport = FakeTransport({"ATMA": LIVE_BUS_TRAFFIC, **responses})
    transport.open()
    driver = ELM327Driver(transport)
    return VagscanSession(
        driver=driver,
        obd2=OBD2Service(driver),
        tp20=TP20Client(driver, bus_listen_seconds=0.05),
        bus_logger=BusLogger(driver),
        dtc_db=DtcDatabase.load_default(),
    ), transport


# The exact frames a monitor-capable adapter would exchange to clear module 15.
_AIRBAG_RESPONSES = {
    "15C0FFFFFFFFFFFF": "215 00 D0 00 03 40 05 00 03",
    "021089FFFFFFFFFF": "215 02 50 89 00 00 00 00",
    "0314FFFFFFFFFFFF": "215 01 54 00 00 00 00 00",
}


def test_clear_airbag_module_end_to_end():
    session, transport = _session(_AIRBAG_RESPONSES)
    session.clear_module_faults("15")  # should not raise
    # The clear request actually went out on the opened channel.
    assert "0314FFFFFFFFFFFF" in transport.written


def test_clear_module_raises_when_module_silent():
    session, _ = _session({"15C0FFFFFFFFFFFF": "NO DATA"})
    with pytest.raises(RuntimeError):
        session.clear_module_faults("15")


def test_clear_module_is_blocked_when_bus_cannot_be_seen():
    """On the clone (no monitor mode) the interlock refuses before any
    transmit - the airbag clear simply can't run there."""
    session, transport = _session({"ATMA": "?", "15C0FFFFFFFFFFFF": "215 00 D0 00 03 40 05 00 03"})
    with pytest.raises(BusSafetyError):
        session.clear_module_faults("15")
    assert "15C0FFFFFFFFFFFF" not in transport.written
