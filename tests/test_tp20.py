"""TP2.0 framing per the public pq-flasher / vag-tool implementations,
exercised against the simulated bus in vagscan.sim.tp20_bus."""
import pytest

from vagscan.elm327.driver import ELM327Driver
from vagscan.sim.tp20_bus import SimModule, SimulatedTP20Bus
from vagscan.vag.bus_logger import parse_raw_frame
from vagscan.vag.tp20 import (
    TP20Client,
    build_setup_request,
    parse_channel_setup,
    segment_message,
)

from .conftest import LIVE_BUS_TRAFFIC, SimBusTransport, sim_bus


def _client(bus=None):
    bus = bus or sim_bus()
    transport = SimBusTransport(bus)
    transport.open()
    return TP20Client(ELM327Driver(transport), bus_listen_seconds=0.05), transport, bus


def test_parse_raw_frame_with_dlc_prefix():
    frame = parse_raw_frame("200 8 01 C0 FF FF FF FF FF FF")
    assert frame.can_id == 0x200
    assert frame.data == bytes([0x01, 0xC0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])


def test_parse_raw_frame_without_dlc_prefix():
    frame = parse_raw_frame("204 01 C0 11 22 33 44 55 66")
    assert frame.data == bytes([0x01, 0xC0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66])


def test_tp20_frame_with_small_first_byte_is_not_taken_as_dlc():
    # op 0 / seq 7 followed by exactly 7 bytes looks like a DLC prefix.
    frame = parse_raw_frame("300 07 00 05 61 3C 17 64 0A", allow_dlc_prefix=False)
    assert frame.data[0] == 0x07 and len(frame.data) == 8


def test_parse_raw_frame_ignores_non_frame_lines():
    assert parse_raw_frame("SEARCHING...") is None
    assert parse_raw_frame("") is None


def test_setup_request_bytes():
    assert build_setup_request("01") == bytes.fromhex("01C00010000301")


def test_parse_channel_setup_reads_both_ids():
    # pq-flasher's example reply: module transmits on 0x300, listens on 0x7A8.
    frame = parse_raw_frame("201 00 D0 00 03 A8 07 01", allow_dlc_prefix=False)
    assert parse_channel_setup([frame], "01") == (0x7A8, 0x300)


def test_parse_channel_setup_rejects_invalid_flag_and_wrong_module():
    invalid = parse_raw_frame("201 00 D0 00 13 A8 07 01", allow_dlc_prefix=False)
    assert parse_channel_setup([invalid], "01") is None
    other = parse_raw_frame("215 00 D0 00 03 A8 07 01", allow_dlc_prefix=False)
    assert parse_channel_setup([other], "01") is None
    assert parse_channel_setup([], "01") is None


def test_segment_single_and_multi_frame():
    frames, nxt = segment_message(bytes([0x21, 0x46]), 0)
    assert frames == [bytes.fromhex("1000022146")] and nxt == 1
    frames, nxt = segment_message(bytes(range(10)), 0)
    assert [f[0] for f in frames] == [0x20, 0x11] and nxt == 2
    assert frames[0][1:3] == b"\x00\x0a"


def test_discover_channel_sends_setup_on_0x200():
    client, transport, _ = _client()
    frames = client.discover_channel("01")
    assert [f.can_id for f in frames] == [0x201]
    assert "ATSH200" in transport.written
    assert "01C00010000301" in transport.written


def test_discover_channel_empty_when_module_absent():
    client, _, _ = _client()
    assert client.discover_channel("03") == []


def test_open_and_round_trip_with_acks():
    client, transport, bus = _client()
    channel = client.discover_and_open("01")
    assert (channel.tx_id, channel.rx_id) == (0x740, 0x300)
    assert "A00F8AFF32FF" in transport.written
    reply = client.send_message(channel, bytes([0x10, 0x89]))
    assert reply == bytes([0x50, 0x89])
    # Our request went out as seq 0, last frame; we ACKed the module's reply.
    assert "1000021089" in transport.written
    assert "B1" in transport.written
    assert channel.tx_seq == 1


def test_multi_frame_reply_is_reassembled():
    client, _, bus = _client()
    channel = client.discover_and_open("01")
    client.send_message(channel, bytes([0x10, 0x89]))
    reply = client.send_message(channel, bytes([0x21, 0x3C]))
    assert reply[:2] == bytes([0x61, 0x3C])
    assert len(reply) > 7  # did not fit in one frame


def test_close_sends_disconnect():
    client, transport, bus = _client()
    channel = client.discover_and_open("01")
    client.close_channel(channel)
    assert "A8" in transport.written
    assert not bus.module(0x01).channel_open
