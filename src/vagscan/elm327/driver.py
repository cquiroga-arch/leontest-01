"""AT-command driver for the ELM327 chipset (and its many clones).

Responsible for: line framing (ELM327 replies terminate each response with
`>` prompt, echo/`\\r` handling), the standard init sequence, protocol
selection, and chip identification/fingerprinting. Deliberately does not
know about OBD-II PIDs or VAG services — see vagscan.obd2 / vagscan.vag for
those, built on top of `send_command` / `send_raw_frame`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from vagscan.transport.base import Transport, TransportClosed

from .protocols import OBDProtocol

logger = logging.getLogger(__name__)

PROMPT = b">"


class ELM327Error(Exception):
    """Adapter reported an error (e.g. `?`, `NO DATA`, `BUS ERROR`, `UNABLE TO CONNECT`)."""


class ELM327Timeout(ELM327Error):
    """No response within the given timeout."""


# ELM327 single-line error tokens (ELM327 datasheet, section on responses).
_KNOWN_ERRORS = {
    "?": "unrecognized command",
    "NO DATA": "no response from vehicle bus (module absent / wrong protocol / engine off)",
    "BUS INIT: ...ERROR": "bus initialization failed",
    "BUS ERROR": "bus error / arbitration lost",
    "CAN ERROR": "CAN controller error",
    "UNABLE TO CONNECT": "could not find a supported protocol",
    "BUFFER FULL": "adapter receive buffer overflowed",
    "STOPPED": "operation interrupted",
    "SEARCHING...": None,  # informational, not a terminal error on its own
}


@dataclass
class AdapterCapabilities:
    """Which optional AT commands this particular adapter implements."""

    headers: bool = False
    can_auto_format_off: bool = False
    set_header: bool = False
    receive_filter: bool = False
    monitor_all: bool = False

    @property
    def can_do_standard_obd(self) -> bool:
        """Reading and clearing fault codes needs none of the above - every
        adapter that talks at all can do it."""
        return True

    @property
    def can_do_vag_probing(self) -> bool:
        """The experimental TP2.0 layer needs raw CAN control, and the safety
        interlock that gates it needs monitor mode. Without monitor_all the
        probe stays disabled by design, not by accident."""
        return self.can_auto_format_off and self.set_header and self.monitor_all

    def missing(self) -> list[str]:
        return [name for name in ("headers", "can_auto_format_off", "set_header", "receive_filter", "monitor_all")
                if not getattr(self, name)]


@dataclass
class ELM327Fingerprint:
    """Raw identification strings. Distinguishing a genuine ELM327 (PIC18F25K80)
    from a clone reliably from software alone isn't possible in general —
    plenty of clones report a plausible-looking `ATI` string on purpose — so
    this is reported to the user as data, not used to gate functionality.
    """

    ati: str  # e.g. "ELM327 v1.5"
    at_device_desc: str  # AT@1, often blank on clones
    at_device_id: str  # AT@2, user-programmable identifier
    voltage: str  # ATRV, sanity-checks that the OBD port actually has 12V present


class ELM327Driver:
    def __init__(self, transport: Transport, *, default_timeout: float = 3.0):
        self._transport = transport
        self._default_timeout = default_timeout
        self._headers_on = False
        self._protocol: OBDProtocol | None = None
        transport.set_reconnect_callback(lambda _t: self.initialize())

    # -- low level ------------------------------------------------------
    def send_command(self, command: str, *, timeout: float | None = None) -> str:
        """Send one AT or OBD-request command, return the adapter's reply
        with echo/prompt/whitespace stripped. Raises ELM327Error/Timeout on
        a recognized error token; callers that need raw multi-line CAN
        responses should still call this (data lines aren't errors).
        """
        timeout = timeout if timeout is not None else self._default_timeout
        if not self._transport.is_open:
            self._transport.open()
        try:
            # write+read is one indivisible exchange - see Transport.transaction
            with self._transport.transaction():
                # Drop any bytes still sitting in the input buffer from a
                # previous command whose reply arrived late (a real car's
                # first request triggers a slow protocol search, and a
                # stray late reply would otherwise be read here and pair
                # every subsequent answer with the wrong question).
                self._transport.reset_input()
                self._transport.write(command.strip().encode("ascii") + b"\r")
                raw = self._transport.read_until(PROMPT, timeout=timeout)
        except TransportClosed as exc:
            raise ELM327Timeout(f"transport closed while waiting for reply to {command!r}") from exc
        if not raw:
            raise ELM327Timeout(f"no response to {command!r} within {timeout}s")
        text = raw.decode("ascii", errors="replace")
        # Strip echo (adapter may echo the command back if ATE1), the
        # trailing '>' prompt, and normalize line endings.
        text = text.replace(command.strip(), "", 1) if text.startswith(command.strip()) else text
        text = text.replace(">", "")
        lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip()]
        # "SEARCHING..." is the adapter negotiating a protocol, not data - drop
        # it so it never gets mistaken for a response.
        lines = [ln for ln in lines if not ln.upper().startswith("SEARCHING")]
        if not lines:
            raise ELM327Timeout(f"no response to {command!r} within {timeout}s")
        for line in lines:
            upper = line.upper()
            for token, meaning in _KNOWN_ERRORS.items():
                if token in upper and meaning is not None:
                    raise ELM327Error(f"{command!r} -> {line!r} ({meaning})")
        return "\n".join(lines)

    # -- init -------------------------------------------------------------
    def initialize(self, *, protocol: OBDProtocol = OBDProtocol.AUTO) -> None:
        """(Re-)apply the standard init sequence. Safe to call again after a
        watchdog-triggered reconnect — it's registered as the transport's
        reconnect callback for exactly that reason."""
        if not self._transport.is_open:
            self._transport.open()
        self._raw_reset()
        self._try("ATE0")  # echo off
        self._try("ATL0")  # linefeeds off
        self._try("ATS0")  # spaces off (denser, easier parsing)
        # Headers OFF for standard OBD-II: we don't need CAN IDs there and
        # they only add ambiguity to parsing (especially on K-line cars).
        # The VAG raw layer turns them back on for itself when it needs them.
        self.set_headers(False)
        self._try(f"ATSP{protocol.value}")
        self._protocol = protocol
        # Warm up the protocol once, now, with a generous timeout: the first
        # bus request on a real car triggers a 1-2s protocol search that
        # answers "SEARCHING...". Doing it here means the search happens
        # during connect instead of derailing the first real data read.
        self._try_warmup()
        logger.info("ELM327 initialized (requested protocol=%s)", protocol.name)

    def _try_warmup(self) -> None:
        try:
            self.send_command("0100", timeout=6.0)  # Mode 01 supported-PIDs; result ignored
        except ELM327Error as exc:
            logger.debug("Protocol warm-up did not complete (harmless): %s", exc)

    def _raw_reset(self) -> None:
        # ATZ (cold reset) takes ~1-2s on most chips and briefly drops the
        # UART, which is noisy on some Bluetooth stacks; ATWS (warm start)
        # is faster and sufficient for a re-init after a reconnect.
        try:
            self.send_command("ATWS", timeout=3.0)
        except ELM327Error:
            self.send_command("ATZ", timeout=3.0)
        time.sleep(0.3)

    def _try(self, command: str) -> str | None:
        try:
            return self.send_command(command)
        except ELM327Error as exc:
            logger.debug("Non-fatal init command failed: %s", exc)
            return None

    def set_headers(self, on: bool) -> None:
        self.send_command("ATH1" if on else "ATH0")
        self._headers_on = on

    @property
    def headers_on(self) -> bool:
        return self._headers_on

    def detected_protocol(self) -> OBDProtocol | None:
        try:
            reply = self.send_command("ATDPN")
        except ELM327Error:
            return None
        return OBDProtocol.from_dpn(reply)

    def read_battery_voltage(self) -> float | None:
        """Volts at the OBD connector, read via ATRV - the adapter's own
        voltage pin. Works on every ELM327 regardless of what the car's ECU
        supports (many older ECUs don't implement the Mode 01 voltage PID),
        which is why the live battery gauge uses this rather than PID 0142."""
        try:
            reply = self.send_command("ATRV")
        except ELM327Error:
            return None
        cleaned = reply.strip().upper().rstrip("V").strip()
        try:
            return round(float(cleaned), 2)
        except ValueError:
            return None

    def probe_capabilities(self) -> "AdapterCapabilities":
        """Ask the adapter which of the non-basic AT commands it actually
        implements, rather than assuming.

        The cheap blue "ELM327 mini" clones - by far the most common thing
        people plug in - implement the standard OBD modes fine but are hit
        and miss on everything else, and they don't announce which parts are
        missing: an unimplemented command just answers `?`. The advanced VAG
        features depend on exactly those commands, and so does the
        listen-before-transmit safety interlock, so it's worth knowing up
        front instead of discovering it against a live car.
        """
        supported: dict[str, bool] = {}
        for name, command, restore in [
            ("headers", "ATH1", None),
            ("can_auto_format_off", "ATCAF0", "ATCAF1"),
            ("set_header", "ATSH7DF", None),
            ("receive_filter", "ATCRA7E8", "ATCRA"),
            ("monitor_all", "ATMA", None),
        ]:
            try:
                reply = self.send_command(command, timeout=2.0)
                supported[name] = "?" not in reply
            except ELM327Error:
                supported[name] = False
            finally:
                if supported.get(name) and name == "monitor_all":
                    # ATMA streams until interrupted; stop it before moving on.
                    self._try(" ")
                if restore:
                    self._try(restore)
        return AdapterCapabilities(**supported)

    def identify(self) -> ELM327Fingerprint:
        return ELM327Fingerprint(
            ati=self._try("ATI") or "",
            at_device_desc=self._try("AT@1") or "",
            at_device_id=self._try("AT@2") or "",
            voltage=self._try("ATRV") or "",
        )

    # -- raw CAN pass-through, used by vagscan.vag ------------------------
    def set_header(self, header_hex: str) -> None:
        """ATSH <header>: set the 11/29-bit CAN ID used for the next
        transmitted frame(s)."""
        self.send_command(f"ATSH{header_hex}")

    def set_receive_filter(self, header_hex: str) -> None:
        """ATCRA <header>: only pass through frames matching this CAN ID."""
        self.send_command(f"ATCRA{header_hex}")

    def clear_receive_filter(self) -> None:
        self._try("ATCRA")

    def send_raw_frame(self, data_hex: str, *, timeout: float | None = None) -> str:
        """Send a hex payload as the current header's CAN frame and return
        the raw (headers-on) reply text, one line per received frame."""
        return self.send_command(data_hex, timeout=timeout)

    def close(self) -> None:
        self._transport.close()
