"""Mode 01 (current data) PID table: SAE J1979 standard PIDs relevant to a
naturally-aspirated 1.6L gasoline engine (Seat Leon 1P 1.6 BSE). Every car
that responds to OBD-II at all supports these regardless of make, unlike the
VAG-specific layer in vagscan.vag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PidDef:
    pid: str  # two hex digits, e.g. "0C"
    name: str
    unit: str
    min_bytes: int
    decode: Callable[[bytes], float]


def _pct(divisor: float) -> Callable[[bytes], float]:
    return lambda b: round((b[0] * 100.0) / divisor, 2)


def _temp_offset40(b: bytes) -> float:
    return b[0] - 40


def _rpm(b: bytes) -> float:
    return round(((b[0] * 256) + b[1]) / 4.0, 1)


def _kpa(b: bytes) -> float:
    return float(b[0])


def _speed_kph(b: bytes) -> float:
    return float(b[0])


def _timing_advance(b: bytes) -> float:
    return round((b[0] / 2.0) - 64, 1)


def _maf_gps(b: bytes) -> float:
    return round(((b[0] * 256) + b[1]) / 100.0, 2)


def _seconds(b: bytes) -> float:
    return float((b[0] * 256) + b[1])


def _km(b: bytes) -> float:
    return float((b[0] * 256) + b[1])


def _voltage(b: bytes) -> float:
    return round(((b[0] * 256) + b[1]) / 1000.0, 3)


def _fuel_trim_pct(b: bytes) -> float:
    return round((b[0] - 128) * 100.0 / 128.0, 2)


PID_TABLE: dict[str, PidDef] = {
    d.pid: d
    for d in [
        PidDef("04", "Engine load", "%", 1, _pct(255.0)),
        PidDef("05", "Coolant temperature", "degC", 1, _temp_offset40),
        PidDef("06", "Short term fuel trim B1", "%", 1, _fuel_trim_pct),
        PidDef("07", "Long term fuel trim B1", "%", 1, _fuel_trim_pct),
        PidDef("0B", "Intake manifold pressure", "kPa", 1, _kpa),
        PidDef("0C", "Engine RPM", "rpm", 2, _rpm),
        PidDef("0D", "Vehicle speed", "km/h", 1, _speed_kph),
        PidDef("0E", "Timing advance", "deg", 1, _timing_advance),
        PidDef("0F", "Intake air temperature", "degC", 1, _temp_offset40),
        PidDef("10", "MAF air flow rate", "g/s", 2, _maf_gps),
        PidDef("11", "Throttle position", "%", 1, _pct(255.0)),
        PidDef("1F", "Time since engine start", "s", 2, _seconds),
        PidDef("21", "Distance traveled with MIL on", "km", 2, _km),
        PidDef("2F", "Fuel tank level input", "%", 1, _pct(255.0)),
        PidDef("33", "Absolute barometric pressure", "kPa", 1, _kpa),
        PidDef("42", "Control module voltage", "V", 2, _voltage),
        PidDef("46", "Ambient air temperature", "degC", 1, _temp_offset40),
        PidDef("5C", "Engine oil temperature", "degC", 1, _temp_offset40),
    ]
}
