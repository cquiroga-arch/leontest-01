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

from .conftest import SimBusTransport, sim_bus


def test_parse_channel_setup_extracts_tx_and_rx():
    frame = parse_raw_frame("215 00 D0 00 03 41 07 01")
    tx, rx = parse_channel_setup([frame], "15")
    assert tx == 0x741  # the id the module listens on
    assert rx == 0x300  # the id the module answers on


def test_parse_channel_setup_none_without_ack():
    frame = parse_raw_frame("280 11 22 33 44 55 66 77 88")
    assert parse_channel_setup([frame]) is None


def test_parse_channel_setup_none_on_empty():
    assert parse_channel_setup([]) is None


def _session(responses):
    transport = SimBusTransport(sim_bus(), {**responses})
    transport.open()
    driver = ELM327Driver(transport)
    return VagscanSession(
        driver=driver,
        obd2=OBD2Service(driver),
        tp20=TP20Client(driver, bus_listen_seconds=0.05),
        bus_logger=BusLogger(driver),
        dtc_db=DtcDatabase.load_default(),
    ), transport


def test_clear_airbag_module_end_to_end():
    session, transport = _session({})
    session.clear_module_faults("15")  # should not raise
    # The clear request reached the airbag module, and nothing else.
    assert transport.bus.module(0x15).faults_cleared
    assert not transport.bus.module(0x01).faults_cleared
    # Channel closed and adapter handed back to OBD-II afterwards.
    assert "A8" in transport.written
    assert "ATCAF1" in transport.written[transport.written.index("A8"):]


def test_clear_module_raises_when_module_silent():
    session, _ = _session({"15C00010000301": "NO DATA"})
    with pytest.raises(RuntimeError):
        session.clear_module_faults("15")


def test_clear_module_is_blocked_when_bus_cannot_be_seen():
    """On the clone (no monitor mode) the interlock refuses before any
    transmit - the airbag clear simply can't run there."""
    session, transport = _session({"ATMA": "?"})
    with pytest.raises(BusSafetyError):
        session.clear_module_faults("15")
    assert "15C00010000301" not in transport.written
