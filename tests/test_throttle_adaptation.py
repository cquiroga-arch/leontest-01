"""Throttle body alignment (basic settings group 060) against the simulated
engine ECU, plus the OBD-II precondition checks and idle measurement."""
import pytest

from vagscan.app.session import VagscanSession
from vagscan.app.throttle import check_preconditions, measure_idle, run_throttle_adaptation
from vagscan.dtc_db import DtcDatabase
from vagscan.elm327.driver import ELM327Driver
from vagscan.obd2 import OBD2Service
from vagscan.vag.bus_logger import BusLogger
from vagscan.vag.measuring import decode_measuring_block
from vagscan.vag.tp20 import BusSafetyError, TP20Client

from .conftest import SimBusTransport, sim_bus

ENGINE_OFF_OK = {
    "010C": "41 0C 00 00",
    "0105": "41 05 6E",  # 70 degC
    "ATRV": "12.4V",
    "03": "NO DATA",
}


def _session(responses=None, **engine):
    transport = SimBusTransport(sim_bus(**engine), {**ENGINE_OFF_OK, **(responses or {})})
    transport.open()
    driver = ELM327Driver(transport)
    session = VagscanSession(
        driver=driver,
        obd2=OBD2Service(driver),
        tp20=TP20Client(driver, bus_listen_seconds=0.05),
        bus_logger=BusLogger(driver),
        dtc_db=DtcDatabase.load_default(),
    )
    return session, transport


def test_preconditions_all_met():
    session, _ = _session()
    pre = check_preconditions(session)
    assert pre.ok, [(c.name, c.detail) for c in pre.checks]


def test_preconditions_block_with_engine_running_and_faults():
    session, _ = _session({"010C": "41 0C 0D 48", "03": "43 01 01 21"})
    pre = check_preconditions(session)
    failed = {c.name for c in pre.checks if not c.ok}
    assert "Motor apagado" in failed
    assert "Sin fallas de motor" in failed


def test_preconditions_block_on_low_battery():
    session, _ = _session({"ATRV": "11.0V"})
    assert not check_preconditions(session).ok


def test_adaptation_runs_to_ok_and_cleans_up():
    session, transport = _session(adaptation_seconds=0.2)
    readings = []
    result = run_throttle_adaptation(
        session, 60, on_reading=lambda t, v: readings.append(v), min_seconds=0.3, max_seconds=5, poll_interval=0.05
    )
    assert result.status == "ok", result.message
    assert "ADP OK" in result.message
    engine = transport.bus.module(0x01)
    assert engine.adapt_stopped  # 32 B8 sent
    assert not engine.channel_open  # A8 sent
    assert "ATCAF1" in transport.written[transport.written.index("A8"):]  # back to OBD-II
    assert any("ADP RUN" in str(v.value) for vals in readings for v in vals)


def test_adaptation_rejected_group_runs_nothing():
    session, transport = _session(reject_basic_settings=True)
    with pytest.raises(RuntimeError, match="098"):
        run_throttle_adaptation(session, 60, min_seconds=0.1, max_seconds=1, poll_interval=0.05)
    engine = transport.bus.module(0x01)
    assert engine.adapt_started is None
    assert not engine.channel_open


def test_adaptation_can_be_cancelled_and_still_stops_basic_settings():
    session, transport = _session(adaptation_seconds=10)
    result = run_throttle_adaptation(session, 60, should_stop=lambda: True, poll_interval=0.05)
    assert result.status == "cancelled"
    assert transport.bus.module(0x01).adapt_stopped


def test_adaptation_refused_on_clone_without_monitor_mode():
    session, transport = _session({"ATMA": "?"})
    with pytest.raises(BusSafetyError):
        run_throttle_adaptation(session, 60)
    assert "01C00010000301" not in transport.written


def test_measure_idle_reports_swing():
    session, _ = _session({"010C": "41 0C 0D 48"})  # 850 rpm
    stats = measure_idle(session, seconds=0.2, interval=0.02)
    assert stats.mean == pytest.approx(850)
    assert stats.swing == 0


def test_decode_text_cells_are_joined():
    body = bytes([0x3C, 0x17, 0x64, 0x0A, 0x11, ord("A"), ord("D"), 0x11, ord("P"), ord(" "), 0x11, ord("O"), ord("K")])
    values = decode_measuring_block(body, 0x3C)
    assert values[0].unit == "%"
    assert values[-1].value == "ADP OK"


def test_unknown_formula_shown_raw_not_guessed():
    values = decode_measuring_block(bytes([0x3C, 0x99, 0x01, 0x02]), 0x3C)
    assert not values[0].known and "crudo" in values[0].display()
