"""Shared test fixtures. FakeTransport is an in-memory stand-in for the
Transport ABC (no real serial port / Bluetooth / hardware involved) so the
driver, OBD2, and VAG-layer logic can be exercised deterministically."""
from __future__ import annotations

import pytest

from vagscan.transport.base import Transport, TransportClosed


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
