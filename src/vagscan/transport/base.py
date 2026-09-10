"""Transport abstraction: anything that can send/receive lines of text to an
ELM327. Kept separate from the AT-command driver so the driver can be tested
against a fake in-memory transport without any real serial port or Bluetooth
hardware involved.
"""
from __future__ import annotations

import abc


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

    def __enter__(self) -> "Transport":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
