"""Raw CAN frame capture, used two ways:

1. Standalone reverse-engineering tool: point it at the car, wiggle whatever
   you're trying to understand (press a button, watch a warning light, clear
   a fault with VCDS if you have it) and record everything so you can later
   correlate frame IDs/bytes with the action. This is how the TP2.0
   constants in tp20.py should be *verified* against this specific car
   rather than trusted blind.
2. A parsing helper for tp20.py's channel-setup discovery, since both need
   to turn ELM327 raw-CAN response lines into (can_id, data) pairs.

ELM327 clones vary a bit in exactly how they format a raw (non-OBD,
ATCAF0-disabled) CAN line - some include a leading DLC byte, some don't, some
pad to 8 data bytes, some don't. `parse_raw_frame` is intentionally
permissive about this rather than assuming one exact layout.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from vagscan.elm327.driver import ELM327Driver, ELM327Error

_HEX_TOKEN = re.compile(r"[0-9A-Fa-f]+")


@dataclass
class CapturedFrame:
    timestamp: float
    can_id: int
    can_id_hex: str
    data: bytes
    raw_line: str


def parse_raw_frame(line: str) -> CapturedFrame | None:
    tokens = _HEX_TOKEN.findall(line)
    if not tokens:
        return None
    id_token = tokens[0]
    if len(id_token) not in (3, 4, 8):
        return None  # not a plausible 11-bit or 29-bit CAN ID
    try:
        can_id = int(id_token, 16)
    except ValueError:
        return None
    rest = tokens[1:]
    # Some firmwares prefix the data with a DLC byte (0-8) before the
    # payload; if the first remaining token is a plausible small DLC and the
    # count of subsequent bytes matches it, treat it as such and drop it.
    if rest and len(rest[0]) <= 2:
        try:
            maybe_dlc = int(rest[0], 16)
        except ValueError:
            maybe_dlc = -1
        if 0 <= maybe_dlc <= 8 and len(rest) - 1 == maybe_dlc:
            rest = rest[1:]
    try:
        data = bytes(int(b, 16) for b in rest if len(b) <= 2)
    except ValueError:
        data = b""
    return CapturedFrame(timestamp=time.time(), can_id=can_id, can_id_hex=id_token.upper(), data=data, raw_line=line)


@dataclass
class BusLogger:
    driver: ELM327Driver
    frames: list[CapturedFrame] = field(default_factory=list)

    def prepare(self) -> None:
        """Disable CAN auto-formatting and any receive filter so every frame
        on the bus is visible in its raw form, headers included."""
        self.driver.set_headers(True)
        self.driver.clear_receive_filter()
        self.driver._try("ATCAF0")  # noqa: SLF001 - intentional low-level access for this one adapter quirk

    def capture_for(self, seconds: float) -> list[CapturedFrame]:
        """ATMA (monitor all) prints every frame it sees until any byte is
        sent to the adapter; we let it run for `seconds` then send a space
        to stop it and read back whatever accumulated.
        """
        self.prepare()
        transport = self.driver._transport  # noqa: SLF001
        transport.write(b"ATMA\r")
        deadline = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            buf += transport.read_until(b"\n", timeout=min(remaining, 0.5))
        transport.write(b" ")  # any character halts ATMA
        try:
            buf += transport.read_until(b">", timeout=1.0)
        except ELM327Error:
            pass
        new_frames = []
        for line in buf.decode("ascii", errors="replace").splitlines():
            frame = parse_raw_frame(line)
            if frame is not None:
                new_frames.append(frame)
        self.frames.extend(new_frames)
        return new_frames

    def to_log_lines(self) -> list[str]:
        return [f"{f.timestamp:.3f} {f.can_id_hex:>4} {f.data.hex().upper()}" for f in self.frames]

    def save(self, path: str) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            for line in self.to_log_lines():
                fh.write(line + "\n")
