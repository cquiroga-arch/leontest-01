"""Transport abstraction: anything that can send/receive lines of text to an
ELM327. Kept separate from the AT-command driver so the driver can be tested
against a fake in-memory transport without any real serial port or Bluetooth
hardware involved.
"""
from __future__ import annotations

import abc
import contextlib


class TransportError(Exception):
    """Raised for I/O failures talking to the adapter."""


class TransportClosed(TransportError):
    """Raised when an operation is attempted on a transport that isn't open,
    or the link dropped mid-operation."""


class Transport(abc.ABC):
    """Byte-oriented duplex link to an ELM327 (or its emulator)."""

    @abc.abstractmethod
    def open(self) -> None:
        ...

    @abc.abstractmethod
    def close(self) -> None:
        ...

    @property
    @abc.abstractmethod
    def is_open(self) -> bool:
        ...

    @abc.abstractmethod
    def write(self, data: bytes) -> None:
        ...

    @abc.abstractmethod
    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        """Read until `terminator` is seen or `timeout` seconds elapse.
        Returns whatever was read (may be a partial/empty read on timeout).
        """

    @contextlib.contextmanager
    def transaction(self):
        """Held across a full request+response exchange.

        The ELM327 is strictly half-duplex: one command, one reply, and it
        has no way to tell you which reply belongs to which command. So any
        second writer (the keep-alive watchdog, most realistically) that
        slips a byte in while someone is waiting for a reply desynchronizes
        the stream, and from then on answers get attributed to the wrong
        question - in a tool that reports fault codes and offers to clear
        them, that's not an acceptable failure mode.

        Transports with no concurrent writers can leave this as the no-op it
        is here.
        """
        yield

    def __enter__(self) -> "Transport":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
