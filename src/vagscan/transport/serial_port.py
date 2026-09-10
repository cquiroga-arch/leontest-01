"""Serial-port transport, used both for a genuine RFCOMM Bluetooth SPP link
(shows up as a COM port on Windows, /dev/rfcomm* or /dev/ttyUSB*-style path
on Linux once paired at the OS level) and for the ELM327 emulator, which
exposes a pty/serial device too.

A background watchdog thread keeps the link alive across the length of a
diagnostic session: it notices a dead socket (write error, or no response to
a periodic no-op probe) and transparently reconnects, so a session doesn't
die because the phone/laptop's Bluetooth stack hiccuped for a second while
you're standing next to the car.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import serial

from .base import Transport, TransportClosed, TransportError

logger = logging.getLogger(__name__)


@dataclass
class SerialTransportConfig:
    port: str
    baudrate: int = 38400
    # ELM327 has no fixed inter-command latency guarantee over Bluetooth SPP;
    # cheap clones can take >1s to reply to a cold AT command, but a stalled
    # response after the link has been active for a while usually means the
    # link died — the watchdog below distinguishes the two by timing since
    # last successful I/O rather than a single fixed timeout.
    read_timeout: float = 2.0
    watchdog_interval: float = 5.0
    watchdog_probe: bytes = b"AT\r"
    reconnect_backoff_initial: float = 1.0
    reconnect_backoff_max: float = 15.0


class SerialTransport(Transport):
    def __init__(self, config: SerialTransportConfig, *, enable_watchdog: bool = True):
        self._config = config
        self._serial: serial.Serial | None = None
        self._lock = threading.RLock()
        self._enable_watchdog = enable_watchdog
        self._watchdog_thread: threading.Thread | None = None
        self._stop_watchdog = threading.Event()
        self._last_activity = 0.0
        self._on_reconnect = None  # optional callback(transport) after a successful reconnect

    def set_reconnect_callback(self, callback) -> None:
        """Called (with `self`) after the watchdog re-establishes a dropped
        link, so the caller (the ELM327 driver) can re-run its init sequence."""
        self._on_reconnect = callback

    def open(self) -> None:
        with self._lock:
            self._open_locked()
        if self._enable_watchdog and self._watchdog_thread is None:
            self._stop_watchdog.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="vagscan-serial-watchdog", daemon=True
            )
            self._watchdog_thread.start()

    def _open_locked(self) -> None:
        cfg = self._config
        self._serial = serial.Serial(
            port=cfg.port,
            baudrate=cfg.baudrate,
            timeout=cfg.read_timeout,
            write_timeout=cfg.read_timeout,
        )
        self._last_activity = time.monotonic()
        logger.info("Opened serial transport on %s @ %d baud", cfg.port, cfg.baudrate)

    def close(self) -> None:
        self._stop_watchdog.set()
        thread = self._watchdog_thread
        self._watchdog_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("Error closing serial port", exc_info=True)
            self._serial = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._serial is not None and self._serial.is_open

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._serial is None:
                raise TransportClosed("serial port is not open")
            try:
                self._serial.write(data)
                self._serial.flush()
            except (serial.SerialException, OSError) as exc:
                self._close_locked()
                raise TransportClosed(f"write failed: {exc}") from exc
            self._last_activity = time.monotonic()

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        with self._lock:
            if self._serial is None:
                raise TransportClosed("serial port is not open")
            ser = self._serial
        deadline = time.monotonic() + timeout
        buf = bytearray()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ser.timeout = min(remaining, self._config.read_timeout)
                chunk = ser.read(1)
                if not chunk:
                    continue
                buf.extend(chunk)
                if buf.endswith(terminator):
                    break
        except (serial.SerialException, OSError) as exc:
            with self._lock:
                self._close_locked()
            raise TransportClosed(f"read failed: {exc}") from exc
        with self._lock:
            self._last_activity = time.monotonic()
        return bytes(buf)

    # -- watchdog -----------------------------------------------------
    def _watchdog_loop(self) -> None:
        cfg = self._config
        backoff = cfg.reconnect_backoff_initial
        while not self._stop_watchdog.wait(cfg.watchdog_interval):
            idle_for = time.monotonic() - self._last_activity
            if idle_for < cfg.watchdog_interval:
                continue  # link has been used recently, no need to probe
            if not self._probe():
                logger.warning("ELM327 link appears dead, attempting reconnect")
                if self._reconnect(backoff):
                    backoff = cfg.reconnect_backoff_initial
                else:
                    backoff = min(backoff * 2, cfg.reconnect_backoff_max)

    def _probe(self) -> bool:
        try:
            self.write(self._config.watchdog_probe)
            reply = self.read_until(b">", timeout=self._config.read_timeout)
            return len(reply) > 0
        except TransportError:
            return False

    def _reconnect(self, backoff: float) -> bool:
        with self._lock:
            self._close_locked()
        time.sleep(backoff)
        try:
            with self._lock:
                self._open_locked()
        except (serial.SerialException, OSError) as exc:
            logger.warning("Reconnect attempt failed: %s", exc)
            return False
        if self._on_reconnect is not None:
            try:
                self._on_reconnect(self)
            except Exception:
                logger.exception("Reconnect callback raised")
        return True
