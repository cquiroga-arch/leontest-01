"""Decode VAG KWP2000 measuring blocks (response `61 <group> ...`).

After the group echo the block is a run of 3-byte cells `<formula> <a> <b>`.
The formula table is the subset documented by TBK/vag-tool (src/blocks.rs),
which itself takes it from jazdw/vag-blocks and seishuku/teensycanbusdisplay.
Formulas not in that table are shown raw rather than guessed.

Formula 0x11 carries two ASCII characters per cell; consecutive text cells
are joined so status strings like "ADP OK" read as one value.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MeasuringValue:
    formula: int
    a: int
    b: int
    value: float | str | None
    unit: str
    known: bool

    def display(self) -> str:
        if isinstance(self.value, str):
            return self.value
        if not self.known:
            return f"{(self.a << 8) | self.b} (crudo, fórmula 0x{self.formula:02X})"
        if self.unit == "bin":
            return f"{(self.a << 8) | self.b:016b}"
        text = f"{self.value:.2f}".rstrip("0").rstrip(".")
        return f"{text} {self.unit}".strip()


def _f(fn, unit):
    return fn, unit


_FORMULAS = {
    0x01: _f(lambda a, b: a * b / 5.0, "rpm"),
    0x04: _f(lambda a, b: abs(b - 127.0) * 0.01 * a, "°"),
    0x05: _f(lambda a, b: a * (b - 100.0) * 0.1, "°C"),
    0x07: _f(lambda a, b: a * b * 0.01, "km/h"),
    0x12: _f(lambda a, b: a * b / 25.0, "mbar"),
    0x14: _f(lambda a, b: a * b / 128.0 - 1.0, "%"),
    0x15: _f(lambda a, b: a * b / 1000.0, "V"),
    0x16: _f(lambda a, b: a * b * 0.001, "ms"),
    0x17: _f(lambda a, b: b * a / 256.0, "%"),
    0x1A: _f(lambda a, b: b - a, "°C"),
    0x21: _f(lambda a, b: 100.0 * b if a == 0 else 100.0 * b / a, "%"),
    0x22: _f(lambda a, b: (b - 128.0) * 0.01 * a, "kW"),
    0x23: _f(lambda a, b: a * b / 100.0, "l/h"),
    0x27: _f(lambda a, b: a * b / 256.0, "mg/stk"),
    0x31: _f(lambda a, b: a * b / 40.0, "mg/stk"),
    0x33: _f(lambda a, b: ((b - 128.0) / 255.0) * a, "mg/stk"),
    0x36: _f(lambda a, b: a * 256.0 + b, ""),
    0x37: _f(lambda a, b: a * b / 200.0, "s"),
    0x60: _f(lambda a, b: a * b * 0.1, "mbar"),
    0x7E: _f(lambda a, b: a * b * 0.1, "g"),
}
_BINARY = {0x08, 0x10, 0x25}
_TEXT = 0x11


def _ascii(byte: int) -> str:
    return chr(byte) if 0x20 <= byte <= 0x7E else "."


def decode_measuring_block(response: bytes, group: int | None = None) -> list[MeasuringValue]:
    """Decode the body of a `21 <group>` reply. `response` is the KWP payload
    after the 0x61 SID (i.e. starting with the group echo)."""
    body = response[1:] if response and (group is None or response[0] == group) else response
    cells = [body[i : i + 3] for i in range(0, len(body) - len(body) % 3, 3)]
    values: list[MeasuringValue] = []
    for formula, a, b in cells:
        if formula == _TEXT:
            text = _ascii(a) + _ascii(b)
            if values and values[-1].formula == _TEXT:
                prev = values.pop()
                text = str(prev.value) + text
            values.append(MeasuringValue(formula, a, b, text, "", True))
        elif formula in _BINARY:
            values.append(MeasuringValue(formula, a, b, float((a << 8) | b), "bin", True))
        elif formula in _FORMULAS:
            fn, unit = _FORMULAS[formula]
            values.append(MeasuringValue(formula, a, b, fn(a, b), unit, True))
        else:
            values.append(MeasuringValue(formula, a, b, None, "", False))
    return values
