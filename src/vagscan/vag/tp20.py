"""EXPERIMENTAL VAG TP2.0 ("Transport Protocol 2.0") channel layer.

Simos 7.1 (and everything else on this car's bus) speaks VAG's own
KWP2000-over-CAN transport for anything beyond the OBD-II-mandated part of
its behavior, and the ELM327 has no built-in support for it - it only knows
the standard ISO 15765-4 OBD transport. Everything here is built on the
ELM327's raw-CAN pass-through (ATSH to set a header, ATCAF0 to turn off its
own frame reassembly, ATMA to monitor) instead.

Read this before trusting any of it:

- The channel-setup broadcast ID (0x200) and probe frame layout below match
  the description of TP2.0 that's most consistently repeated across
  hobbyist reverse-engineering write-ups. Nobody here has a VAG-issued spec
  to check that against, so treat it as an informed first guess, not fact.
- `discover_channel()` therefore does NOT claim to know the resulting tx/rx
  CAN IDs on its own. It sends the guessed probe and hands back every raw
  frame the car replied with, unvalidated, for you (or a capture compared
  against a known-working tool) to interpret. Only once you've confirmed
  real tx/rx IDs from an actual capture on *this* car should you build a
  `TP20Channel` via `open_channel()` and trust `send_message`.
- `send_message` only implements single-frame TP2.0 messages (payload fits
  in one CAN frame). Multi-frame segmentation's exact block-size/ack byte
  layout is exactly the kind of detail that needs a validated capture
  before it's worth encoding as "the" algorithm - see the NotImplementedError
  below for where to add it once you have that capture.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from vagscan.elm327.driver import ELM327Driver, ELM327Error

from .bus_logger import BusLogger, CapturedFrame, parse_raw_frame

logger = logging.getLogger(__name__)

CHANNEL_SETUP_BROADCAST_ID = "200"
_SETUP_OPCODE = 0xC0
_MAX_SINGLE_FRAME_PAYLOAD = 7  # 1 length byte + up to 7 data bytes per classic 8-byte CAN frame


class TP20Error(Exception):
    pass


class TP20Timeout(TP20Error):
    pass


class BusSafetyError(TP20Error):
    """Raised instead of transmitting when the CAN ID we were about to use
    is already being broadcast by a real module on this car."""


@dataclass(frozen=True)
class TP20Channel:
    module_address: str
    tx_id: int
    rx_id: int
    block_size: int = 0
    sep_time_ms: int = 10
    validated: bool = False


class TP20Client:
    def __init__(self, driver: ELM327Driver, bus_logger: BusLogger | None = None, *, bus_listen_seconds: float = 2.0):
        self._driver = driver
        self._logger = bus_logger or BusLogger(driver)
        self._channel: TP20Channel | None = None
        self._verified_free_ids: set[int] = set()
        self._observed_ids: set[int] | None = None
        self._bus_listen_seconds = bus_listen_seconds

    def assert_transmit_id_is_free(self, can_id: int, *, listen_seconds: float | None = None) -> set[int]:
        """Listen before transmitting.

        Everything else this tool sends is standard OBD-II, addressed the way
        every scan tool addresses it. The TP2.0 probe is the one exception:
        it puts a frame on an ID we *believe* is the diagnostic channel-setup
        broadcast, based on public reverse-engineering rather than a spec we
        can check. If that belief is wrong for this particular car and the ID
        actually belongs to some module's periodic traffic, we'd be a second
        transmitter on an ID a real receiver is consuming.

        So: watch the bus first, and refuse to transmit on an ID that's
        already in use. Returns every CAN ID observed, which also tells the
        caller the bus is genuinely alive.
        """
        listen_seconds = self._bus_listen_seconds if listen_seconds is None else listen_seconds
        if self._observed_ids is None:
            self._observed_ids = self._logger.observed_can_ids(listen_seconds)
        if not self._observed_ids:
            # Seeing nothing is not the same as seeing an idle bus. A VAG
            # powertrain bus with the ignition on is never silent, so an
            # empty observation means we couldn't see the bus at all -
            # typically a clone adapter without working monitor mode, or the
            # ignition off. Either way we have no basis to call the ID free,
            # and guessing in the permissive direction is the one mistake
            # this interlock exists to prevent.
            raise BusSafetyError(
                "No se transmitió nada: no se vio ninguna trama en el bus, así que no hay forma de "
                "confirmar que el ID CAN 0x{:03X} esté libre. Suele ser un adaptador clon sin modo monitor "
                "(ATMA) funcional, o el contacto apagado / bus dormido. La sonda TP2.0 queda deshabilitada: "
                "el OBD-II estándar sigue andando normal.".format(can_id)
            )
        if can_id in self._observed_ids:
            raise BusSafetyError(
                f"No se transmitió nada: el ID CAN 0x{can_id:03X} ya lo está usando un módulo real de este "
                f"auto (se lo vio en el bus durante {listen_seconds:g}s). Transmitir ahí sería pisar tráfico "
                "legítimo, así que la sonda TP2.0 queda deshabilitada para este vehículo."
            )
        self._verified_free_ids.add(can_id)
        return self._observed_ids

    def forget_bus_observation(self) -> None:
        """Drop the cached bus snapshot so the next transmit re-listens."""
        self._observed_ids = None
        self._verified_free_ids.clear()

    def discover_channel(self, module_address: str, *, timeout: float = 1.0) -> list[CapturedFrame]:
        """Send the best-guess TP2.0 channel-setup probe for `module_address`
        (two hex digits, e.g. "01" for the engine) and return every frame
        seen in response within `timeout` seconds. Empty list means the
        module didn't answer at all (wrong address, module asleep/absent,
        or the guessed probe format doesn't match what it expects)."""
        addr = int(module_address, 16)
        # Interlock: never the first thing we do on a car is transmit.
        self.assert_transmit_id_is_free(int(CHANNEL_SETUP_BROADCAST_ID, 16))
        self._logger.prepare()
        self._driver.set_header(CHANNEL_SETUP_BROADCAST_ID)
        try:
            self._driver.send_command("ATSTFF")  # ~1020ms adapter-side wait, to not miss a slow ECU
        except ELM327Error:
            pass
        data_hex = f"{addr:02X}{_SETUP_OPCODE:02X}FFFFFFFFFFFF"
        try:
            raw = self._driver.send_raw_frame(data_hex, timeout=timeout)
        except ELM327Error as exc:
            logger.info("No reply captured for module %s setup probe: %s", module_address, exc)
            return []
        frames = [f for f in (parse_raw_frame(line) for line in raw.splitlines()) if f is not None]
        self._logger.frames.extend(frames)
        return frames

    def open_channel(
        self,
        module_address: str,
        tx_id: int,
        rx_id: int,
        *,
        block_size: int = 0,
        sep_time_ms: int = 10,
    ) -> TP20Channel:
        """Build a channel from tx/rx CAN IDs you've already confirmed
        (typically via `discover_channel` plus a real capture)."""
        channel = TP20Channel(
            module_address=module_address,
            tx_id=tx_id,
            rx_id=rx_id,
            block_size=block_size,
            sep_time_ms=sep_time_ms,
            validated=True,
        )
        self._channel = channel
        return channel

    def send_message(self, channel: TP20Channel, payload: bytes, *, timeout: float = 2.0) -> bytes:
        if len(payload) > _MAX_SINGLE_FRAME_PAYLOAD:
            raise NotImplementedError(
                f"payload of {len(payload)} bytes needs TP2.0 multi-frame segmentation, "
                "which isn't implemented yet - see the tp20.py module docstring. "
                "Capture a known multi-frame exchange on this car first and encode the "
                "real block-size/ack byte layout here rather than guessing it."
            )
        self.assert_transmit_id_is_free(channel.tx_id)
        self._driver.set_header(f"{channel.tx_id:03X}")
        frame_hex = f"{len(payload):02X}" + payload.hex().upper()
        frame_hex = frame_hex.ljust(16, "F")  # pad to 8 bytes like the setup probe
        try:
            raw = self._driver.send_raw_frame(frame_hex, timeout=timeout)
        except ELM327Error as exc:
            raise TP20Timeout(f"no reply on rx id {channel.rx_id:03X}: {exc}") from exc
        frames = [f for f in (parse_raw_frame(line) for line in raw.splitlines()) if f is not None]
        self._logger.frames.extend(frames)
        reply = next((f for f in frames if f.can_id == channel.rx_id), None)
        if reply is None:
            raise TP20Timeout(f"no frame from expected rx id {channel.rx_id:03X} (saw {[f.can_id_hex for f in frames]})")
        length = reply.data[0] if reply.data else 0
        return reply.data[1 : 1 + length]

    def keep_alive(self, channel: TP20Channel) -> None:
        """TP2.0 channels are expected to time out and close after a period
        of inactivity (a T3-style idle timer, in every description of this
        protocol we could find) - call this periodically during a long
        session. What exactly should be sent as a no-op keepalive frame is
        one more thing to confirm from a real capture; for now this resends
        an empty-payload message, which is a reasonable guess but, like the
        rest of this module, unverified against the real ECU.
        """
        try:
            self.send_message(channel, b"", timeout=0.5)
        except TP20Error:
            logger.debug("keep_alive probe on channel %s got no reply (may be normal)", channel.module_address)

    def close_channel(self) -> None:
        self._channel = None
