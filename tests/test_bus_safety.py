"""The safety interlocks that stand between this tool and someone's car.

Everything else the app sends is standard OBD-II. These cover the one
non-standard thing it can transmit, plus the operation that could leave a
module locked out.
"""
import pytest

from vagscan.elm327.driver import ELM327Driver
from vagscan.vag.kwp2000 import KWP2000Client, KWP2000Error
from vagscan.vag.tp20 import BusSafetyError, TP20Client

from .conftest import FakeTransport


def _client(responses, **kwargs):
    transport = FakeTransport(responses)
    transport.open()
    driver = ELM327Driver(transport)
    return TP20Client(driver, bus_listen_seconds=0.05, **kwargs), transport


def test_refuses_to_transmit_on_a_can_id_a_real_module_is_using():
    """If 0x200 turns out to carry a module's real traffic on this car, we
    must not become a second transmitter on it."""
    client, transport = _client({"ATMA": "200 01 02 03 04 05 06 07\n280 AA BB CC DD EE FF 11"})
    with pytest.raises(BusSafetyError) as exc_info:
        client.discover_channel("01")

    assert "0x200" in str(exc_info.value)
    # The decisive assertion: the probe frame never went out.
    assert "01C00010000301" not in transport.written


def test_refuses_to_transmit_when_the_bus_cannot_be_seen_at_all():
    """Regression guard for a real false-safe: an adapter that can't monitor
    (the common blue clones often can't) returns an empty observation, and
    an empty observation was being read as "the ID is free". A VAG bus with
    the ignition on is never silent, so seeing nothing means we're blind -
    which is not permission to transmit."""
    client, transport = _client({"ATMA": "?", "01C00010000301": "NO DATA"})
    with pytest.raises(BusSafetyError, match="ninguna trama"):
        client.discover_channel("01")

    assert "01C00010000301" not in transport.written


def test_transmits_when_the_target_id_is_not_in_use():
    client, transport = _client(
        {
            "ATMA": "280 AA BB CC DD EE FF 11\n320 01 02 03 04 05 06 07",
            "01C00010000301": "201 00 D0 00 03 40 07 01",
        }
    )
    frames = client.discover_channel("01")

    assert [f.can_id_hex for f in frames] == ["201"]
    assert "01C00010000301" in transport.written


def test_listens_before_the_first_transmission_not_after():
    """Ordering matters: the listen has to happen before any frame goes out,
    otherwise the interlock is decorative."""
    client, transport = _client(
        {"ATMA": "280 AA BB CC DD EE FF 11", "01C00010000301": "201 00 D0 00 03 40 07 01"}
    )
    client.discover_channel("01")

    assert transport.written.index("ATMA") < transport.written.index("01C00010000301")


def test_opening_a_channel_is_also_gated_not_just_discovery():
    """The tester TX id the module assigns is checked too: if a real module
    already broadcasts on it, not even the channel-parameter frame goes out."""
    client, transport = _client({"ATMA": "740 01 02 03 04 05 06 07"})
    with pytest.raises(BusSafetyError):
        client.open_channel("01", tx_id=0x740, rx_id=0x300)

    assert not any(w.startswith("A0") for w in transport.written)


def test_send_message_is_gated():
    from vagscan.vag.tp20 import TP20Channel

    client, transport = _client({"ATMA": "740 01 02 03 04 05 06 07"})
    with pytest.raises(BusSafetyError):
        client.send_message(TP20Channel("01", 0x740, 0x300), bytes([0x10, 0x89]))
    assert not any(w.startswith("1000021089") for w in transport.written)


def test_bus_observation_is_reused_instead_of_relistening_per_module():
    """A 17-module sweep must not mean 17 separate listen windows."""
    responses = {"ATMA": "280 AA BB CC DD EE FF 11"}
    for address in ("01", "03", "15"):
        responses[f"{int(address, 16):02X}C00010000301"] = "NO DATA"
    client, transport = _client(responses)

    for address in ("01", "03", "15"):
        client.discover_channel(address)

    assert transport.written.count("ATMA") == 1


def test_forgetting_the_observation_forces_a_fresh_listen():
    responses = {"ATMA": "280 AA BB CC DD EE FF 11", "01C00010000301": "NO DATA"}
    client, transport = _client(responses)

    client.discover_channel("01")
    client.forget_bus_observation()
    client.discover_channel("01")

    assert transport.written.count("ATMA") == 2


def test_security_access_refuses_to_run_without_explicit_opt_in():
    """Wrong keys here increment a lockout counter on the real module, so it
    must be impossible to reach by accident."""
    from vagscan.vag.tp20 import TP20Channel

    client, _ = _client({})
    kwp = KWP2000Client(client, TP20Channel("01", 0x740, 0x300))

    with pytest.raises(KWP2000Error, match="bloqueado"):
        kwp.security_access_request_seed(0x01)
    with pytest.raises(KWP2000Error, match="bloqueado"):
        kwp.security_access_send_key(0x01, b"\x00\x00")


def test_security_access_sends_nothing_when_refused():
    from vagscan.vag.tp20 import TP20Channel

    client, transport = _client({})
    kwp = KWP2000Client(client, TP20Channel("01", 0x740, 0x300))

    with pytest.raises(KWP2000Error):
        kwp.security_access_request_seed(0x01)

    assert not any("27" in w for w in transport.written)
