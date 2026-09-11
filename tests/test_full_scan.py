from vagscan.app.full_scan import FullScanner
from vagscan.app.session import VagscanSession
from vagscan.dtc_db import DtcDatabase
from vagscan.elm327.driver import ELM327Driver
from vagscan.obd2.service import OBD2Service
from vagscan.vag.addresses import MODULE_ADDRESSES
from vagscan.vag.bus_logger import BusLogger
from vagscan.vag.tp20 import TP20Client

from .conftest import FakeTransport


def _silent_module_probes(except_for: dict[str, str] | None = None) -> dict[str, str]:
    """Responses for every VAG module's channel-setup probe. A module that
    isn't there answers "NO DATA" (the adapter's way of saying nobody
    replied) - local AT commands still answer OK, which is why this can't
    just be a blanket default."""
    answers = {}
    for address in MODULE_ADDRESSES:
        probe_hex = f"{int(address, 16):02X}C0FFFFFFFFFFFF"
        answers[probe_hex] = "NO DATA"
    answers.update(except_for or {})
    return answers


def _session_with(responses, default="OK"):
    transport = FakeTransport(responses, default=default)
    transport.open()
    driver = ELM327Driver(transport)
    return VagscanSession(
        driver=driver,
        obd2=OBD2Service(driver),
        tp20=TP20Client(driver),
        bus_logger=BusLogger(driver),
        dtc_db=DtcDatabase.load_default(),
    ), transport


def test_scan_collects_dtcs_with_knowledge_base_info():
    session, _ = _session_with(
        {
            "ATI": "ELM327 v1.5",
            "ATRV": "12.6V",
            "ATDPN": "A6",
            "03": "43 03 00 01 71",
            "07": "NO DATA",
            "0A": "NO DATA",
        }
    )
    result = FullScanner(session, scan_vag_modules=False).run()

    codes = [found.dtc.code for found in result.dtcs]
    assert codes == ["P0300", "P0171"]
    assert all(found.kind == "Almacenada" for found in result.dtcs)
    assert "Misfire" in result.dtcs[0].title
    assert result.dtcs[0].severity == "severe"
    assert result.adapter == "ELM327 v1.5"
    assert result.voltage == "12.6V"
    assert result.protocol == "ISO_15765_4_CAN_11BIT_500K"


def test_scan_reports_progress_for_every_step():
    session, _ = _session_with({"03": "NO DATA", "07": "NO DATA", "0A": "NO DATA"})
    scanner = FullScanner(session, scan_vag_modules=False)
    steps = []
    result = scanner.run(progress=lambda label, current, total: steps.append((label, current, total)))

    assert result.dtcs == []
    assert [s[2] for s in steps] == [scanner.total_steps()] * len(steps)
    assert steps[0][1] == 1
    assert "Escaneo completo" in steps[-1][0]


def test_scan_survives_a_mode_that_errors_out():
    """A bus error on one mode must not abort the whole scan - the rest of
    the sweep still has to run and report what it found."""
    session, _ = _session_with({"03": "43 01 33", "07": "BUS ERROR", "0A": "NO DATA"})
    result = FullScanner(session, scan_vag_modules=False).run()

    assert [f.dtc.code for f in result.dtcs] == ["P0133"]
    assert any("Pendiente" in w for w in result.warnings)


def test_scan_probes_every_known_vag_module():
    session, _ = _session_with(
        {"03": "NO DATA", "07": "NO DATA", "0A": "NO DATA", **_silent_module_probes()}
    )
    result = FullScanner(session, scan_vag_modules=True).run()

    assert len(result.modules) == len(MODULE_ADDRESSES)
    assert {m.address for m in result.modules} == set(MODULE_ADDRESSES)
    assert result.responding_modules == []  # nothing answered the probe


def test_scan_marks_a_module_that_answers_as_responding():
    session, _ = _session_with(
        {
            "03": "NO DATA",
            "07": "NO DATA",
            "0A": "NO DATA",
            **_silent_module_probes(except_for={"01C0FFFFFFFFFFFF": "204 01 C0 11 22 33 44 55 66"}),
        }
    )
    result = FullScanner(session, scan_vag_modules=True).run()

    responding = result.responding_modules
    assert [m.address for m in responding] == ["01"]
    assert responding[0].frames[0].can_id_hex == "204"


def test_scan_can_be_cancelled_midway():
    session, _ = _session_with({"03": "NO DATA", "07": "NO DATA", "0A": "NO DATA"})
    scanner = FullScanner(session, scan_vag_modules=True)
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2

    result = scanner.run(should_stop=should_stop)
    assert any("cancelado" in w.lower() for w in result.warnings)
    assert len(result.modules) < len(MODULE_ADDRESSES)


def test_summary_line_reads_naturally_for_both_outcomes():
    session, _ = _session_with({"03": "NO DATA", "07": "NO DATA", "0A": "NO DATA"})
    clean = FullScanner(session, scan_vag_modules=False).run()
    assert "Sin fallas" in clean.summary_line()

    session2, _ = _session_with({"03": "43 01 33", "07": "NO DATA", "0A": "NO DATA"})
    dirty = FullScanner(session2, scan_vag_modules=False).run()
    assert "1 falla" in dirty.summary_line()
