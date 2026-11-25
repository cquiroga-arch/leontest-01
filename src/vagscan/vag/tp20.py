"""VAG TP2.0 ("Transport Protocol 2.0") over CAN, on an ELM327's raw-CAN mode.

Simos 7.1 (and every other module on this car) speaks KWP2000 over VAG's own
TP2.0 transport for anything beyond the OBD-II-mandated part of its
behaviour. The ELM327 has no built-in TP2.0 support, so this drives it in
raw pass-through mode: ATSH sets the CAN ID we transmit on, ATCRA filters
what we receive, ATCAF0 turns off the adapter's own ISO-TP formatting so the
bytes we hand it go out exactly as given.

Where the byte layout comes from
--------------------------------
The framing below follows two independent public implementations that agree
with each other: I-CAN-hack/pq-flasher (tp20.py, Python) and TBK/vag-tool
(src/tp20.rs, Rust), both written against PQ35-platform cars - the same
platform as this Leon 1P. It has not yet been captured on *this* car, so
treat it as "documented by two working tools", not "verified here".

- Channel setup: tester sends `<module> C0 00 10 00 03 01` on 0x200. The
  module answers on 0x200 + module with `00 D0 <rx lo> <rx hi> <tx lo> <tx hi> 01`:
  the first id is the one the module transmits on (our RX), the second the
  one it listens on (our TX). A nibble 0x1 in the top of either id means
  "invalid".
- Channel parameters: `A0 0F 8A FF 32 FF` (block size 15, T1 100 ms, T3
  5 ms); the module answers `A1 ...` with its own timings.
- Data: first byte is `<op><seq>`; seq counts mod 16 per direction. The
  first frame of a message carries a 2-byte big-endian payload length.
  op 0x2 = more frames follow, no ACK wanted; 0x1 = last frame, ACK wanted;
  0x0 = ACK wanted, more follow; 0x3 = last, no ACK.
- ACK: `B<seq+1>` where seq is the last data frame received.
- Keep-alive `A3` (answered with `A1 ...`), disconnect `A8`.

Bus safety
----------
Before the first transmit on any CAN ID, the bus is monitored and the ID is
refused if a real module already uses it - see assert_transmit_id_is_free.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from vagscan.elm327.driver import ELM327Driver, ELM327Error

from .bus_logger import BusLogger, CapturedFrame, parse_raw_frame

logger = logging.getLogger(__name__)

CHANNEL_SETUP_BROADCAST_ID = "200"
_SETUP_OPCODE = 0xC0
_SETUP_ACK_OPCODE = 0xD0
# The id we ask the module to transmit its replies on (pq-flasher/vag-tool use 0x300).
_REQUESTED_MODULE_TX_ID = 0x300
CHANNEL_PARAMS_REQUEST = bytes([0xA0, 0x0F, 0x8A, 0xFF, 0x32, 0xFF])
_KEEPALIVE = bytes([0xA3])
_DISCONNECT = bytes([0xA8])
_FRAME_DATA_BYTES = 7
_MAX_FRAMES_WITHOUT_ACK = 15  # we request block size 15 in CHANNEL_PARAMS_REQUEST


def build_setup_request(module_address: str) -> bytes:
    addr = int(module_address, 16)
    tx_lo, tx_hi = _REQUESTED_MODULE_TX_ID & 0xFF, _REQUESTED_MODULE_TX_ID >> 8
    return bytes([addr, _SETUP_OPCODE, 0x00, 0x10, tx_lo, tx_hi, 0x01])


def parse_channel_setup(frames: "list[CapturedFrame]", module_address: str | None = None) -> "tuple[int, int] | None":
    """Return (tester_tx_id, tester_rx_id) from a channel-setup reply, or
    None if no frame is a valid positive setup response.

    Layout `00 D0 <A lo> <A hi> <B lo> <B hi> 01`: A is the id the module
    transmits on (our RX), B the id it listens on (our TX). Ids with any of
    the top four bits set are flagged invalid by the protocol and rejected.
    """
    expected_id = 0x200 + int(module_address, 16) if module_address else None
    for frame in frames:
        data = frame.data
        if expected_id is not None and frame.can_id != expected_id:
            continue
        if len(data) < 6 or data[1] != _SETUP_ACK_OPCODE:
            continue
        rx_id = int.from_bytes(data[2:4], "little")
        tx_id = int.from_bytes(data[4:6], "little")
        if rx_id & 0xF000 or tx_id & 0xF000 or not (0 < rx_id <= 0x7FF and 0 < tx_id <= 0x7FF):
            continue
        return tx_id, rx_id
    return None


def segment_message(payload: bytes, start_seq: int) -> "tuple[list[bytes], int]":
    """Split a KWP2000 payload into TP2.0 data frames. Returns the frames and
    the next sequence number to use. Only the last frame asks for an ACK."""
    if len(payload) > 0xFFFF:
        raise ValueError("payload too long for TP2.0")
    body = len(payload).to_bytes(2, "big") + payload
    chunks = [body[i : i + _FRAME_DATA_BYTES] for i in range(0, len(body), _FRAME_DATA_BYTES)]
    if len(chunks) > _MAX_FRAMES_WITHOUT_ACK:
        raise ValueError(f"payload needs {len(chunks)} frames; more than one block isn't implemented")
    frames = []
    seq = start_seq & 0xF
    for i, chunk in enumerate(chunks):
        opcode = 0x10 if i == len(chunks) - 1 else 0x20
        frames.append(bytes([opcode | seq]) + chunk)
        seq = (seq + 1) & 0xF
    return frames, seq


class TP20Error(Exception):
    pass


class TP20Timeout(TP20Error):
    pass


class BusSafetyError(TP20Error):
    """Raised instead of transmitting when the CAN ID we were about to use
    is already being broadcast by a real module on this car, or when the bus
    can't be seen at all."""


@dataclass
class TP20Channel:
    module_address: str
    tx_id: int
    rx_id: int
    validated: bool = False
    tx_seq: int = field(default=0, compare=False)
    open: bool = field(default=True, compare=False)


class TP20Client:
    def __init__(self, driver: ELM327Driver, bus_logger: BusLogger | None = None, *, bus_listen_seconds: float = 2.0):
        self._driver = driver
        self._logger = bus_logger or BusLogger(driver)
        self._channel: TP20Channel | None = None
        self._verified_free_ids: set[int] = set()
        self._observed_ids: set[int] | None = None
        self._bus_listen_seconds = bus_listen_seconds
        self._header: int | None = None
        self._filter: int | None = None
        self._raw_mode = False
        self._inbox: list[CapturedFrame] = []

    # -- bus-safety interlock ------------------------------------------
    def assert_transmit_id_is_free(self, can_id: int, *, listen_seconds: float | None = None) -> set[int]:
        """Listen before transmitting, and refuse to transmit on an ID that a
        real module already uses, or when the bus can't be seen at all (a
        clone without monitor mode, or the ignition off). Returns every CAN
        ID observed."""
        listen_seconds = self._bus_listen_seconds if listen_seconds is None else listen_seconds
        if self._observed_ids is None:
            self._observed_ids = self._logger.observed_can_ids(listen_seconds)
            # The logger cleared the receive filter and changed modes.
            self._header = self._filter = None
            self._raw_mode = False
        if not self._observed_ids:
            raise BusSafetyError(
                "No se transmitió nada: no se vio ninguna trama en el bus, así que no hay forma de "
                "confirmar que el ID CAN 0x{:03X} esté libre. Suele ser un adaptador clon sin modo monitor "
                "(ATMA) funcional, o el contacto apagado / bus dormido. La capa VAG queda deshabilitada: "
                "el OBD-II estándar sigue andando normal.".format(can_id)
            )
        if can_id in self._observed_ids:
            raise BusSafetyError(
                f"No se transmitió nada: el ID CAN 0x{can_id:03X} ya lo está usando un módulo real de este "
                f"auto (se lo vio en el bus durante {listen_seconds:g}s). Transmitir ahí sería pisar tráfico "
                "legítimo, así que la capa VAG queda deshabilitada para este vehículo."
            )
        self._verified_free_ids.add(can_id)
        return self._observed_ids

    def forget_bus_observation(self) -> None:
        self._observed_ids = None
        self._verified_free_ids.clear()

    # -- raw adapter plumbing --------------------------------------------
    def _configure(self, tx_id: int, rx_id: int) -> None:
        if not self._raw_mode:
            self._driver.set_headers(True)
            self._driver._try("ATS1")  # noqa: SLF001 - spaces between id and bytes
            self._driver._try("ATCAF0")  # noqa: SLF001 - send our bytes verbatim
            self._raw_mode = True
        if self._header != tx_id:
            self._driver.set_header(f"{tx_id:03X}")
            self._header = tx_id
        if self._filter != rx_id:
            self._driver.set_receive_filter(f"{rx_id:03X}")
            self._filter = rx_id

    def _transmit(self, data: bytes, rx_id: int, *, timeout: float = 1.0) -> list[CapturedFrame]:
        """Send one frame on the configured header; queue and return any
        frames from `rx_id` the adapter printed while waiting."""
        try:
            raw = self._driver.send_raw_frame(data.hex().upper(), timeout=timeout)
        except ELM327Error as exc:
            if "NO DATA" in str(exc):
                return []
            raise TP20Timeout(str(exc)) from exc
        frames = [f for f in (_parse(line) for line in raw.splitlines()) if f is not None]
        self._logger.frames.extend(frames)
        mine = [f for f in frames if f.can_id == rx_id and f.data]
        self._inbox.extend(mine)
        return mine

    def _monitor(self, rx_id: int, seconds: float) -> None:
        """Passively wait for more frames from `rx_id` (ATMA with the receive
        filter already set). Stops early once a frame that ends a message or
        an ACK is seen."""
        transport = self._driver._transport  # noqa: SLF001
        buf = b""
        with transport.transaction():
            transport.write(b"ATMA\r")
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                chunk = transport.read_until(b"\n", timeout=min(max(deadline - time.monotonic(), 0.01), 0.1))
                buf += chunk
                if chunk and _ends_wait(buf, rx_id):
                    break
            transport.write(b" ")
            try:
                buf += transport.read_until(b">", timeout=1.0)
            except ELM327Error:
                pass
        for line in buf.decode("ascii", errors="replace").splitlines():
            frame = _parse(line)
            if frame is not None and frame.can_id == rx_id and frame.data:
                self._logger.frames.append(frame)
                self._inbox.append(frame)

    def _next_frame(self, rx_id: int, deadline: float) -> CapturedFrame:
        while not self._inbox:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TP20Timeout(f"sin respuesta en el ID 0x{rx_id:03X}")
            self._monitor(rx_id, min(remaining, 0.5))
        return self._inbox.pop(0)

    # -- channel lifecycle -------------------------------------------------
    def discover_channel(self, module_address: str, *, timeout: float = 1.0) -> list[CapturedFrame]:
        """Send the TP2.0 channel-setup request to `module_address` and return
        the frames the module answered with (empty if nobody answered)."""
        self.assert_transmit_id_is_free(int(CHANNEL_SETUP_BROADCAST_ID, 16))
        reply_id = 0x200 + int(module_address, 16)
        self._configure(int(CHANNEL_SETUP_BROADCAST_ID, 16), reply_id)
        self._inbox.clear()
        try:
            frames = self._transmit(build_setup_request(module_address), reply_id, timeout=timeout)
        except TP20Timeout as exc:
            logger.info("No reply to channel setup for module %s: %s", module_address, exc)
            frames = []
        self._inbox.clear()
        return frames

    def discover_and_open(self, module_address: str, *, timeout: float = 1.0) -> "TP20Channel | None":
        frames = self.discover_channel(module_address, timeout=timeout)
        parsed = parse_channel_setup(frames, module_address)
        if parsed is None:
            return None
        tx_id, rx_id = parsed
        return self.open_channel(module_address, tx_id, rx_id)

    def open_channel(self, module_address: str, tx_id: int, rx_id: int, *, timeout: float = 1.0) -> TP20Channel:
        """Exchange channel parameters on an already set-up channel and return
        it ready for send_message. Raises TP20Timeout if the module doesn't
        answer the parameter request with A1."""
        self.assert_transmit_id_is_free(tx_id)
        self._configure(tx_id, rx_id)
        self._inbox.clear()
        self._transmit(CHANNEL_PARAMS_REQUEST, rx_id, timeout=timeout)
        deadline = time.monotonic() + timeout
        while True:
            frame = self._next_frame(rx_id, deadline)
            if frame.data[0] == 0xA1:
                break
        channel = TP20Channel(module_address=module_address, tx_id=tx_id, rx_id=rx_id, validated=True)
        self._channel = channel
        return channel

    def close_channel(self, channel: TP20Channel | None = None) -> None:
        channel = channel or self._channel
        if channel is not None and channel.open:
            try:
                self._configure(channel.tx_id, channel.rx_id)
                self._transmit(_DISCONNECT, channel.rx_id, timeout=0.3)
            except (TP20Error, ELM327Error):
                pass
            channel.open = False
        self._inbox.clear()
        self._channel = None

    def keep_alive(self, channel: TP20Channel) -> None:
        self._configure(channel.tx_id, channel.rx_id)
        self._transmit(_KEEPALIVE, channel.rx_id, timeout=0.5)
        deadline = time.monotonic() + 0.5
        try:
            while True:
                frame = self._next_frame(channel.rx_id, deadline)
                if frame.data[0] == 0xA1:
                    return
                self._inbox.insert(0, frame)
                return
        except TP20Timeout:
            logger.debug("keep-alive on module %s got no A1", channel.module_address)

    def restore_obd_mode(self) -> None:
        """Put the adapter back the way standard OBD-II expects it."""
        self._driver._try("ATCAF1")  # noqa: SLF001
        self._driver._try("ATS0")  # noqa: SLF001
        self._driver.clear_receive_filter()
        self._driver._try("ATSH7DF")  # noqa: SLF001
        try:
            self._driver.set_headers(False)
        except ELM327Error:
            pass
        self._header = self._filter = None
        self._raw_mode = False

    # -- messaging ---------------------------------------------------------
    def send_message(self, channel: TP20Channel, payload: bytes, *, timeout: float = 2.0) -> bytes:
        """Send one KWP2000 request and return the module's reply payload."""
        self.assert_transmit_id_is_free(channel.tx_id)
        if not channel.open:
            raise TP20Error("el canal TP2.0 está cerrado")
        self._configure(channel.tx_id, channel.rx_id)
        frames, next_seq = segment_message(payload, channel.tx_seq)
        self._inbox.clear()
        for frame in frames:
            self._transmit(frame, channel.rx_id, timeout=timeout)
        channel.tx_seq = next_seq
        expected_ack = 0xB0 | next_seq
        deadline = time.monotonic() + timeout
        while True:
            frame = self._next_frame(channel.rx_id, deadline)
            head = frame.data[0]
            if head == expected_ack:
                break
            if head & 0xF0 == 0xB0:
                raise TP20Error(f"ACK con secuencia inesperada: {head:02X} (se esperaba {expected_ack:02X})")
            if head == 0xA8:
                channel.open = False
                raise TP20Error("el módulo cerró el canal TP2.0")
            # A data frame before the ACK: keep it for receive_message.
            self._inbox.insert(0, frame)
            break
        return self.receive_message(channel, timeout=timeout)

    def receive_message(self, channel: TP20Channel, *, timeout: float = 2.0) -> bytes:
        """Reassemble the next message from the module, ACKing as asked."""
        deadline = time.monotonic() + timeout
        body = bytearray()
        while True:
            frame = self._next_frame(channel.rx_id, deadline)
            head = frame.data[0]
            op, seq = head >> 4, head & 0xF
            if head == 0xA3:
                self._transmit(bytes([0xA1]) + CHANNEL_PARAMS_REQUEST[1:], channel.rx_id)
                continue
            if head == 0xA8:
                channel.open = False
                raise TP20Error("el módulo cerró el canal TP2.0")
            if op in (0xB, 0x9, 0xA):
                continue  # stray ACK / parameter frame
            if op not in (0x0, 0x1, 0x2, 0x3):
                continue
            body.extend(frame.data[1:])
            if op in (0x0, 0x1):
                self._transmit(bytes([0xB0 | ((seq + 1) & 0xF)]), channel.rx_id, timeout=0.3)
            if op in (0x1, 0x3):
                if len(body) < 2:
                    raise TP20Error("mensaje TP2.0 demasiado corto")
                length = int.from_bytes(body[:2], "big")
                payload = bytes(body[2 : 2 + length])
                if len(payload) < length:
                    raise TP20Error(f"mensaje TP2.0 incompleto: {len(payload)} de {length} bytes")
                return payload


def _ends_wait(buf: bytes, rx_id: int) -> bool:
    for line in buf.decode("ascii", errors="replace").splitlines():
        frame = _parse(line)
        if frame is None or frame.can_id != rx_id or not frame.data:
            continue
        op = frame.data[0] >> 4
        if op in (0x1, 0x3, 0xA, 0xB):
            return True
    return False


def _parse(line: str) -> "CapturedFrame | None":
    # TP2.0 frames are variable length and their first byte is often small
    # (an opcode/sequence like 0x07), which the generic DLC-prefix heuristic
    # would mistake for a length byte. ATD1 is never enabled, so no DLC.
    return parse_raw_frame(line, allow_dlc_prefix=False)
