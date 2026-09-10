from vagscan.elm327.driver import ELM327Driver
from vagscan.vag.bus_logger import parse_raw_frame
from vagscan.vag.tp20 import TP20Client

from .conftest import FakeTransport


def test_parse_raw_frame_with_dlc_prefix():
    frame = parse_raw_frame("200 8 01 C0 FF FF FF FF FF FF")
    assert frame.can_id == 0x200
    assert frame.can_id_hex == "200"
    assert frame.data == bytes([0x01, 0xC0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])


def test_parse_raw_frame_without_dlc_prefix():
    frame = parse_raw_frame("204 01 C0 11 22 33 44 55 66")
    assert frame.can_id == 0x204
    assert frame.data == bytes([0x01, 0xC0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66])


def test_parse_raw_frame_ignores_non_frame_lines():
    assert parse_raw_frame("SEARCHING...") is None
    assert parse_raw_frame("") is None


def test_discover_channel_returns_candidate_frames():
    transport = FakeTransport(
        {
            "01C0FFFFFFFFFFFF": "204 01 C0 11 22 33 44 55 66",
        }
    )
    transport.open()
    driver = ELM327Driver(transport)
    client = TP20Client(driver)
    frames = client.discover_channel("01", timeout=1.0)
    assert len(frames) == 1
    assert frames[0].can_id_hex == "204"
    assert "ATSH200" in transport.written


def test_discover_channel_empty_when_no_reply():
    transport = FakeTransport({"01C0FFFFFFFFFFFF": "NO DATA"})
    transport.open()
    driver = ELM327Driver(transport)
    client = TP20Client(driver)
    assert client.discover_channel("01") == []


def test_send_message_round_trip():
    transport = FakeTransport(
        {
            # StartDiagnosticSession(0x89) request, single frame: len=02, then 10 89
            "021089FFFFFFFFFF": "310 02 50 89 FF FF FF FF FF",
        }
    )
    transport.open()
    driver = ELM327Driver(transport)
    client = TP20Client(driver)
    channel = client.open_channel("01", tx_id=0x300, rx_id=0x310)
    reply = client.send_message(channel, bytes([0x10, 0x89]))
    assert reply == bytes([0x50, 0x89])
    assert "ATSH300" in transport.written


def test_send_message_oversized_payload_raises_not_implemented():
    transport = FakeTransport()
    transport.open()
    driver = ELM327Driver(transport)
    client = TP20Client(driver)
    channel = client.open_channel("01", tx_id=0x300, rx_id=0x310)
    try:
        client.send_message(channel, bytes(range(8)))
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass
