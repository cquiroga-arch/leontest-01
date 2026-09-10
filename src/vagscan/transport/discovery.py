"""Helpers to find a paired ELM327 among the host's serial ports.

Pairing itself is left to the OS Bluetooth stack (Windows Settings > Bluetooth
& devices, or `bluetoothctl` on Linux) — once paired, classic SPP Bluetooth
devices show up as a regular serial port, which is what we scan here.
"""
from __future__ import annotations

from dataclasses import dataclass

from serial.tools import list_ports

# Common vendor strings seen in paired ELM327 dongles' serial port
# descriptions across OSes. Used only to rank candidates for the user to
# confirm — never to auto-select silently, since a wrong port for a serial
# scanner is at worst a timeout, but we still want an explicit choice.
_ELM_HINTS = ("elm327", "obdii", "obd2", "obd-ii", "vlink", "obdlink", "vgate")


@dataclass
class PortInfo:
    device: str
    description: str
    hwid: str
    likely_elm327: bool


def list_serial_ports() -> list[PortInfo]:
    ports = []
    for p in list_ports.comports():
        desc = (p.description or "").lower()
        likely = any(hint in desc for hint in _ELM_HINTS)
        ports.append(
            PortInfo(device=p.device, description=p.description or "", hwid=p.hwid or "", likely_elm327=likely)
        )
    return ports


def guess_elm327_port() -> str | None:
    """Best-effort guess of the ELM327's serial port. Returns None if nothing
    looks like a match, or more than one candidate is equally likely — the
    caller should fall back to asking the user in that case."""
    candidates = [p for p in list_serial_ports() if p.likely_elm327]
    if len(candidates) == 1:
        return candidates[0].device
    return None
