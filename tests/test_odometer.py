"""Read-only odometer reading from the instrument cluster.

There is no write path being tested here because there is no write path -
changing a stored odometer is fraud and isn't implemented. These cover the
read and its decode.
"""
import pytest

from vagscan.elm327.driver import ELM327Driver
from vagscan.vag.kwp2000 import KWP2000Client, KWP2000Error, OdometerReading, _decode_odometer_km
from vagscan.vag.tp20 import TP20Client

from vagscan.sim.tp20_bus import SimModule, SimulatedTP20Bus

from .conftest import LIVE_BUS_TRAFFIC, SimBusTransport


def _kwp(canned):
    cluster = SimModule(0x17, tester_tx=0x742, canned={bytes.fromhex(k): bytes.fromhex(v) for k, v in canned.items()})
    bus = SimulatedTP20Bus(LIVE_BUS_TRAFFIC.split("\r"), [cluster])
    transport = SimBusTransport(bus)
    transport.open()
    tp20 = TP20Client(ELM327Driver(transport), bus_listen_seconds=0.05)
    channel = tp20.discover_and_open("17")
    return KWP2000Client(tp20, channel), transport


def test_decode_finds_plausible_km_little_endian():
    # 0x0212F5 LE = 0xF5 0x12 0x02 -> 135925 km
    assert _decode_odometer_km(bytes([0xF5, 0x12, 0x02])) == 135925


def test_decode_rejects_implausible_values():
    # All 0xFF would decode to something astronomically large - must be rejected.
    assert _decode_odometer_km(bytes([0xFF, 0xFF, 0xFF])) is None


def test_decode_empty_is_none():
    assert _decode_odometer_km(b"") is None


def test_read_odometer_positive_response():
    # measuring block 0x22: response SID 0x61, then block bytes with km 135925
    kwp, _ = _kwp({"2122": "6122F5120200"})
    reading = kwp.read_odometer()
    assert isinstance(reading, OdometerReading)
    assert reading.km == 135925
    assert reading.confident
    assert reading.source_group == 0x22


def test_read_odometer_falls_through_candidate_groups():
    # First candidate group (0x22) is rejected by the ECU, second (0x02) answers.
    kwp, _ = _kwp(
        {
            "2122": "7F2131",  # requestOutOfRange
            "2102": "6102A0860100",  # 0x0186A0 = 100000
        }
    )
    reading = kwp.read_odometer()
    assert reading.km == 100000
    assert reading.source_group == 0x02


def test_read_odometer_keeps_raw_when_nothing_decodes():
    # Block echoes group 0x22 then two zero bytes -> nothing plausible.
    kwp, _ = _kwp({"2122": "61220000"})
    reading = kwp.read_odometer()
    assert reading.km is None
    assert not reading.confident
    assert reading.raw == bytes([0x22, 0x00, 0x00])


def test_read_odometer_raises_when_cluster_never_answers():
    kwp, _ = _kwp(
        {
            "2122": "7F2131",
            "2102": "7F2131",
            "2101": "7F2131",
        }
    )
    with pytest.raises(KWP2000Error):
        kwp.read_odometer()


def test_there_is_no_write_odometer_method():
    """Guards intent: if someone ever adds a mileage-writing method, this
    fails and forces a conversation about it."""
    methods = [n for n in dir(KWP2000Client) if callable(getattr(KWP2000Client, n)) and not n.startswith("__")]
    for name in methods:
        low = name.lower()
        assert not (("write" in low or "set" in low or "adjust" in low) and ("odom" in low or "mileage" in low or "km" in low)), name
