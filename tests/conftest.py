"""Shared test fixtures. FakeTransport is an in-memory stand-in for the
Transport ABC (no real serial port / Bluetooth / hardware involved) so the
driver, OBD2, and VAG-layer logic can be exercised deterministically."""
from __future__ import annotations

import pytest

from vagscan.transport.base import Transport, TransportClosed


# What a live VAG powertrain bus looks like to ATMA: constant periodic
# traffic from real modules. Tests that exercise transmitting need this,
# because the bus-safety interlock refuses to transmit when it can't see any
# traffic at all (an empty bus means "blind", not "idle"). Deliberately does
# not include 0x200 or 0x300, the IDs the TP2.0 layer transmits on.
LIVE_BUS_TRAFFIC = "\r".join(
    [
        "280 49 0E 00 00 00 00 00 1A",
        "288 00 00 00 00 00 00 00 00",
        "320 05 00 00 00 00 00 00 00",
        "420 00 00 00 00 00 00 00 00",
        "5A0 FF 00 00 00 00 00 00 00",
    ]
)


class FakeTransport(Transport):
    """Maps an exact outgoing command string to a canned reply string.
    Unmapped commands get `default` (a generic non-error "OK"-ish reply).
    """

    def __init__(self, responses: dict[str, str] | None = None, default: str = "OK"):
        self.responses = dict(responses or {})
        self.default = default
        self._open = False
        self.written: list[str] = []
        self._pending_reply = b""
        self._reconnect_cb = None

    def set_reconnect_callback(self, callback) -> None:
        self._reconnect_cb = callback

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportClosed("closed")
        cmd = data.decode("ascii").strip()
        self.written.append(cmd)
        reply_text = self.responses.get(cmd, self.default)
        self._pending_reply = (reply_text + "\r>").encode("ascii")

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        if not self._open:
            raise TransportClosed("closed")
        data = self._pending_reply
        self._pending_reply = b""
        return data


@pytest.fixture
def fake_transport():
    transport = FakeTransport()
    transport.open()
    return transport


class SimBusTransport(FakeTransport):
    """FakeTransport whose raw-CAN commands are answered by a simulated
    TP2.0 bus (vagscan.sim.tp20_bus); everything else falls back to the
    canned `responses` map like FakeTransport."""

    def __init__(self, bus, responses: dict[str, str] | None = None, default: str = "OK"):
        super().__init__(responses, default)
        self.bus = bus

    def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportClosed("closed")
        cmd = data.decode("ascii").strip()
        if not cmd:  # the space that stops ATMA
            self._pending_reply = b">"
            return
        self.written.append(cmd)
        if cmd in self.responses:
            reply = self.responses[cmd]
        else:
            reply = self.bus.handle(cmd)
            if reply is None:
                reply = self.default
        self._pending_reply = (reply + "\r>").encode("ascii")


def sim_bus(**engine_kwargs):
    from vagscan.sim.tp20_bus import SimModule, SimulatedTP20Bus

    return SimulatedTP20Bus(
        LIVE_BUS_TRAFFIC.split("\r"),
        [SimModule(0x01, tester_tx=0x740, **engine_kwargs), SimModule(0x15, tester_tx=0x741)],
    )
