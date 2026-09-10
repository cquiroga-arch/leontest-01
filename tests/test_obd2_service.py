import pytest

from vagscan.elm327.driver import ELM327Driver
from vagscan.obd2.service import OBD2Service, ObdRequestError, decode_dtcs

from .conftest import FakeTransport


def test_decode_dtcs_single_code():
    assert [d.code for d in decode_dtcs([0x01, 0x33])] == ["P0133"]


def test_decode_dtcs_skips_padding_pairs():
    dtcs = decode_dtcs([0x00, 0x00, 0x01, 0x33])
    assert [d.code for d in dtcs] == ["P0133"]


def test_decode_dtcs_covers_all_letter_prefixes():
    # top 2 bits of the first byte select P/C/B/U per SAE J2012
    assert decode_dtcs([0x00, 0x01])[0].code == "P0001"
    assert decode_dtcs([0x41, 0x01])[0].code == "C0101"
    assert decode_dtcs([0x81, 0x01])[0].code == "B0101"
    assert decode_dtcs([0xC1, 0x01])[0].code == "U0101"


def _service_with(responses):
    transport = FakeTransport(responses)
    transport.open()
    return OBD2Service(ELM327Driver(transport))


def test_read_pid_decodes_rpm():
    service = _service_with({"010C": "41 0C 1A F8"})
    assert service.read_pid("0C") == 1726.0


def test_read_pid_decodes_coolant_temp_with_offset():
    service = _service_with({"0105": "41 05 5A"})  # 0x5A=90 -> 90-40=50 degC
    assert service.read_pid("05") == 50


def test_read_pid_unknown_pid_raises():
    service = _service_with({})
    with pytest.raises(ObdRequestError):
        service.read_pid("ZZ")


def test_read_pids_reports_unsupported_as_none_instead_of_raising():
    service = _service_with({"010C": "NO DATA"})
    result = service.read_pids(["0C"])
    assert result == {"0C": None}


def test_read_stored_dtcs():
    service = _service_with({"03": "43 01 33 02 71"})
    codes = [d.code for d in service.read_stored_dtcs()]
    assert codes == ["P0133", "P0271"]


def test_read_vin():
    vin = "WVWZZZ1KZ8W000001"
    payload_bytes = [0x49, 0x02, 0x01] + [ord(c) for c in vin]
    response = " ".join(f"{b:02X}" for b in payload_bytes)
    service = _service_with({"0902": response})
    assert service.read_vin() == vin


def test_clear_dtcs_sends_mode_04():
    transport = FakeTransport({"04": "44"})
    transport.open()
    service = OBD2Service(ELM327Driver(transport))
    service.clear_dtcs()
    assert "04" in transport.written
