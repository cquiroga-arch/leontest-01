"""Fault-code knowledge base: generic SAE J2012 codes plus VAG-specific
guidance. Deliberately a flat JSON file + in-memory dict rather than a
database engine - the dataset is small, read-mostly, and hand-edited, so a
query engine would be pure overhead. Extend it by editing the JSON files in
dtc_db/data/ directly.
"""
from __future__ import annotations

import importlib.resources
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DtcInfo:
    code: str | None
    system: str
    title: str
    description: str
    common_causes: list[str] = field(default_factory=list)
    suggested_checks: list[str] = field(default_factory=list)
    severity: str = "informational"
    source: str = ""
    module_address: str | None = None
    module_name: str | None = None
    vag_fault_code: str | None = None


def _data_dir_when_frozen() -> Path | None:
    """PyInstaller unpacks bundled data next to the running executable (in
    `sys._MEIPASS`). Package-resource lookup usually still works there, but
    "usually" isn't good enough for the file that holds every fault-code
    description - without it the app runs and silently reports every code as
    unknown."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    return Path(base) / "vagscan" / "dtc_db" / "data"


def _load_json(filename: str) -> list[dict]:
    frozen_dir = _data_dir_when_frozen()
    if frozen_dir is not None:
        candidate = frozen_dir / filename
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    with importlib.resources.files("vagscan.dtc_db.data").joinpath(filename).open("r", encoding="utf-8") as f:
        return json.load(f)


_SYSTEM_LETTER = {
    "P": "Motor / transmisión (Powertrain)",
    "C": "Chasis (Chassis): frenos, ABS, dirección, suspensión",
    "B": "Carrocería (Body): airbag, confort, luces, tablero",
    "U": "Red de comunicación entre módulos (bus CAN)",
}

# Third character of a Pxxxx code -> functional area (SAE J2012). Approximate:
# it places the code in the right neighbourhood when we have no exact entry.
_P_GROUP = {
    "0": "Controles auxiliares de emisiones y de red",
    "1": "Medición de aire/combustible",
    "2": "Medición de aire/combustible (circuito de inyectores)",
    "3": "Sistema de encendido o fallo de combustión (misfire)",
    "4": "Controles auxiliares de emisiones (EGR, EVAP, sondas, catalizador)",
    "5": "Velocidad, ralentí y entradas auxiliares",
    "6": "Computadora de a bordo y sus salidas",
    "7": "Transmisión",
    "8": "Transmisión",
    "9": "Transmisión / cambios",
}


def describe_code(code: str) -> DtcInfo:
    """Best-effort description of ANY DTC from its structure, for codes that
    aren't in the curated database. Never returns None - a code the tool
    can't fully explain still gets its system area and whether it's a
    generic (SAE) or manufacturer-specific code, which beats a blank."""
    code = code.strip().upper()
    letter = code[0] if code else "P"
    system = _SYSTEM_LETTER.get(letter, "Desconocido")
    area = ""
    if letter == "P" and len(code) >= 3 and code[2].isdigit():
        area = _P_GROUP.get(code[2], "")
    standard = "genérico (definición estándar SAE)"
    if len(code) >= 2 and code[1] == "1":
        standard = "específico del fabricante (VAG) - la definición exacta varía por modelo"
    title = area or system
    description = (
        f"Código {standard}. Área: {system}." + (f" {area}." if area else "")
        + " No está en la base local con detalle, así que esta es una lectura orientativa por la "
        "estructura del código; el número es correcto, buscá su significado exacto para tu Seat León."
    )
    return DtcInfo(
        code=code,
        system=letter,
        title=title,
        description=description,
        common_causes=[],
        suggested_checks=["Buscar el significado específico de este código para el Seat León 1.6 BSE"],
        severity="unknown",
        source="descripción automática por estructura del código (aproximada)",
    )


def _entry_to_info(entry: dict) -> DtcInfo | None:
    if "_meta" in entry:
        return None  # documentation-only entry, not a real DTC record
    return DtcInfo(
        code=entry.get("code"),
        system=entry.get("system", entry.get("module_name", "unknown")),
        title=entry["title"],
        description=entry.get("description", ""),
        common_causes=entry.get("common_causes", []),
        suggested_checks=entry.get("suggested_checks", []),
        severity=entry.get("severity", "informational"),
        source=entry.get("source", ""),
        module_address=entry.get("module_address"),
        module_name=entry.get("module_name"),
        vag_fault_code=entry.get("vag_fault_code"),
    )


class DtcDatabase:
    def __init__(self, entries: list[DtcInfo] | None = None):
        self._by_code: dict[str, list[DtcInfo]] = {}
        self._all: list[DtcInfo] = []
        if entries:
            for e in entries:
                self._add(e)

    def _add(self, info: DtcInfo) -> None:
        self._all.append(info)
        if info.code:
            self._by_code.setdefault(info.code.upper(), []).append(info)

    @classmethod
    def load_default(cls) -> "DtcDatabase":
        db = cls()
        for filename in ("generic_codes.json", "vag_codes.json"):
            for raw_entry in _load_json(filename):
                info = _entry_to_info(raw_entry)
                if info is not None:
                    db._add(info)
        return db

    def lookup(self, code: str) -> list[DtcInfo]:
        """All known entries for an exact code (usually one; VAG guidance
        entries without an SAE code are only reachable via `search`/`by_module`)."""
        return list(self._by_code.get(code.upper(), []))

    def describe(self, code: str) -> DtcInfo:
        """Always returns something: the curated entry when we have one, or a
        best-effort description built from the code's structure otherwise, so
        a scanned code is never shown blank."""
        matches = self.lookup(code)
        return matches[0] if matches else describe_code(code)

    def has(self, code: str) -> bool:
        return bool(self.lookup(code))

    def by_module(self, module_address: str) -> list[DtcInfo]:
        return [e for e in self._all if e.module_address == module_address.upper()]

    def search(self, query: str) -> list[DtcInfo]:
        q = query.lower()
        return [
            e
            for e in self._all
            if q in (e.code or "").lower()
            or q in e.title.lower()
            or q in e.description.lower()
            or q in (e.module_name or "").lower()
        ]

    def __len__(self) -> int:
        return len(self._all)
