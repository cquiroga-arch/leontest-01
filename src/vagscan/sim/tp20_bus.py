"""A simulated VAG CAN bus behind a monitor-capable ELM327, speaking TP2.0 +
KWP2000 the way pq-flasher / vag-tool describe it.

Used by the test-suite and by tools/car_emulator.py --genuine. It encodes
*our understanding* of the protocol, so passing against it proves the app
is self-consistent with that understanding - not that the real Simos 7.1
agrees. Only the car can confirm that.

Group 060 (throttle body alignment) is modelled as: two throttle-angle
cells (formula 0x17, %), a learn-step counter (0x36) and a status text in
0x11 cells - "ADP RUN" while adapting, "ADP OK" when done. The real ECU's
cell layout for this group has not been captured; the app displays whatever
cells come back, so a different layout only changes what's shown.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


def _hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def _cells_text(text: str) -> bytes:
    if len(text) % 2:
        text += " "
    out = b""
    for i in range(0, len(text), 2):
        out += bytes([0x11, ord(text[i]), ord(text[i + 1])])
    return out


@dataclass
class SimModule:
    address: int
    tester_tx: int  # id the module listens on
    module_tx: int = 0x300  # id the module answers on
    adaptation_seconds: float = 3.0
    adaptation_group: int = 0x3C
    reject_basic_settings: bool = False
    canned: dict = field(default_factory=dict)  # request bytes -> reply bytes, checked first
    # state
    channel_open: bool = False
    rx_body: bytearray = field(default_factory=bytearray)
    tx_seq: int = 0
    session: int = 0x81
    adapt_started: float | None = None
    adapt_stopped: bool = False
    faults_cleared: bool = False
    requests: list[bytes] = field(default_factory=list)

    def _frames(self, payload: bytes) -> list[bytes]:
        body = len(payload).to_bytes(2, "big") + payload
        chunks = [body[i : i + 7] for i in range(0, len(body), 7)]
        out = []
        for i, chunk in enumerate(chunks):
            op = 0x10 if i == len(chunks) - 1 else 0x20
            out.append(bytes([op | self.tx_seq]) + chunk)
            self.tx_seq = (self.tx_seq + 1) & 0xF
        return out

    def adaptation_state(self) -> str:
        if self.adapt_started is None:
            return "ADP --"
        if time.monotonic() - self.adapt_started >= self.adaptation_seconds:
            return "ADP OK"
        return "ADP RUN"

    def kwp(self, req: bytes) -> bytes:
        self.requests.append(req)
        if bytes(req) in self.canned:
            return self.canned[bytes(req)]
        sid = req[0]
        if sid == 0x10:
            self.session = req[1]
            return bytes([0x50, req[1]])
        if sid == 0x14:
            self.faults_cleared = True
            return bytes([0x54]) + req[1:3]
        if sid == 0x18:
            return bytes([0x58, 0x00])
        if sid == 0x21:
            group = req[1]
            if self.address == 0x01 and group == self.adaptation_group:
                state = self.adaptation_state()
                angle = 0x0A if state != "ADP OK" else 0x0C
                steps = 0 if self.adapt_started is None else min(8, int((time.monotonic() - self.adapt_started) * 3))
                body = bytes([0x17, 0x64, angle, 0x17, 0x64, 0xF2, 0x36, 0x00, steps]) + _cells_text(state)
                return bytes([0x61, group]) + body
            if self.address == 0x01 and group == 0x01:
                return bytes([0x61, group, 0x01, 0xC8, 0x8C, 0x05, 0x0A, 0xBE, 0x17, 0x64, 0x0A, 0x21, 0x00, 0x00])
            return bytes([0x7F, 0x21, 0x31])
        if sid == 0x31 and len(req) >= 4 and req[2] == 0x00:
            if self.reject_basic_settings or self.address != 0x01 or req[3] != self.adaptation_group:
                return bytes([0x7F, 0x31, 0x31])
            if self.session != 0x89:
                return bytes([0x7F, 0x31, 0x22])
            if req[1] == 0xBA:
                return bytes([0x71, 0xBA])
            if req[1] == 0xB9:
                self.adapt_started = time.monotonic()
                return bytes([0x7F, 0x31, 0x78]) + b""  # pending, real answer follows
            return bytes([0x7F, 0x31, 0x12])
        if sid == 0x32 and len(req) >= 4 and req[1] == 0xB8:
            self.adapt_stopped = True
            return bytes([0x72, 0xB8])
        return bytes([0x7F, sid, 0x11])

    def on_frame(self, data: bytes) -> list[bytes]:
        """Handle one frame from the tester, return frames we send back."""
        head = data[0]
        if head == 0xA0:
            self.channel_open = True
            return [bytes([0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF])]
        if head == 0xA3:
            return [bytes([0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF])]
        if head == 0xA8:
            self.channel_open = False
            return []
        op, seq = head >> 4, head & 0xF
        if op == 0xB:
            return []
        if not self.channel_open:
            return []
        self.rx_body.extend(data[1:])
        if op in (0x2, 0x3, 0x0):
            return []
        body, self.rx_body = bytes(self.rx_body), bytearray()
        length = int.from_bytes(body[:2], "big")
        req = body[2 : 2 + length]
        out = [bytes([0xB0 | ((seq + 1) & 0xF)])]
        reply = self.kwp(req)
        out += self._frames(reply)
        if reply[:1] == b"\x7f" and reply[2:3] == b"\x78":
            # The deferred real answer to 31 B9.
            out += self._frames(bytes([0x71, 0xB9]))
        return out


class SimulatedTP20Bus:
    """Answers the ELM327 raw-CAN commands (ATSH/ATCRA/ATMA/hex frames)."""

    def __init__(self, background_traffic: list[str], modules: list[SimModule] | None = None):
        self.background_traffic = background_traffic
        self.modules = {m.address: m for m in (modules or [])}
        self.header = 0x7DF
        self.filter: int | None = None
        self.pending: list[tuple[int, bytes]] = []

    def module(self, address: int) -> SimModule:
        return self.modules[address]

    def _lines(self, frames: list[tuple[int, bytes]]) -> list[str]:
        return [f"{can_id:03X} {_hex(data)}" for can_id, data in frames if self.filter in (None, can_id)]

    def handle(self, cmd: str) -> str | None:
        """Return the adapter's reply text, or None if `cmd` isn't ours."""
        up = cmd.upper().replace(" ", "")
        if up.startswith("ATSH"):
            self.header = int(up[4:], 16)
            return "OK"
        if up.startswith("ATCRA"):
            self.filter = int(up[5:], 16) if len(up) > 5 else None
            return "OK"
        if up == "ATMA":
            frames, self.pending = self.pending, []
            lines = self._lines(frames)
            if self.filter is None:
                lines = self.background_traffic + lines
            return "\r".join(lines) if lines else "NO DATA"
        if up.startswith("AT") or not all(c in "0123456789ABCDEF" for c in up) or len(up) % 2:
            return None
        if self.header == 0x7DF or self.header >= 0x7E0:
            return None  # standard OBD-II, handled elsewhere
        data = bytes.fromhex(up)
        replies: list[tuple[int, bytes]] = []
        if self.header == 0x200:
            mod = self.modules.get(data[0])
            if mod is not None and len(data) >= 7 and data[1] == 0xC0:
                payload = bytes([0x00, 0xD0, mod.module_tx & 0xFF, mod.module_tx >> 8, mod.tester_tx & 0xFF, mod.tester_tx >> 8, 0x01])
                replies.append((0x200 + mod.address, payload))
        else:
            for mod in self.modules.values():
                if mod.tester_tx == self.header:
                    replies += [(mod.module_tx, f) for f in mod.on_frame(data)]
        # Like the real adapter: print what arrives within its timeout. To
        # exercise the receive path we hand back only the first frame now and
        # leave the rest for a later ATMA, as a slow ECU would.
        if not replies:
            return "NO DATA"
        now, later = replies[:1], replies[1:]
        self.pending.extend(later)
        lines = self._lines(now)
        return "\r".join(lines) if lines else "NO DATA"
