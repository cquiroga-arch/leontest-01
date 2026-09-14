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
