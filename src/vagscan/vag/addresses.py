"""VAG diagnostic module logical addresses. These 2-digit hex addresses are
long-standing, widely published VAG conventions (the same list any VCDS/
VAG-COM module selector shows) and stable across this vehicle generation -
unlike the byte-level TP2.0/KWP2000 framing details in tp20.py/kwp2000.py,
these addresses aren't in question.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleInfo:
    address: str  # two hex digits, e.g. "01"
    name: str
    notes: str = ""


MODULE_ADDRESSES: dict[str, ModuleInfo] = {
    m.address: m
    for m in [
        ModuleInfo("01", "Engine", "Simos 7.1 on this vehicle"),
        ModuleInfo("02", "Auto Transmission"),
        ModuleInfo("03", "ABS/ESP Brakes"),
        ModuleInfo("08", "Auto HVAC"),
        ModuleInfo("15", "Airbag/SRS", "Pyrotechnic squib circuits live here - see safety notes in vagscan.dtc_db"),
        ModuleInfo("16", "Steering Wheel/Column Electronics"),
        ModuleInfo("17", "Instrument Cluster", "Often integrates immobilizer function on this generation"),
        ModuleInfo("19", "CAN Gateway"),
        ModuleInfo("25", "Immobilizer", "Out of scope for this project - see README"),
        ModuleInfo("35", "Central Convenience/Comfort"),
        ModuleInfo("36", "Seat Memory (Driver)"),
        ModuleInfo("42", "Door Electronics (Driver)"),
        ModuleInfo("46", "Central Locking/Comfort System"),
        ModuleInfo("52", "Door Electronics (Passenger)"),
        ModuleInfo("56", "Radio/Infotainment"),
        ModuleInfo("76", "Parking Aid"),
        ModuleInfo("77", "Telephone"),
    ]
}
