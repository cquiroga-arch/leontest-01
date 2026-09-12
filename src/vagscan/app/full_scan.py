"""One-button full vehicle scan.

Sweeps everything the adapter can reach in a single pass: the generic
OBD-II side (VIN, stored/pending/permanent DTCs) which works on any
compliant car, then each known VAG module address to see which ones answer
at all. Reports partial results rather than failing outright - a module
that doesn't answer, or a mode the ECU doesn't implement, is an expected
outcome of scanning, not an error that should abort the rest.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from vagscan.dtc_db import DtcInfo
from vagscan.elm327.driver import ELM327Error
from vagscan.obd2.service import DTC
from vagscan.vag.addresses import MODULE_ADDRESSES
from vagscan.vag.bus_logger import CapturedFrame
from vagscan.vag.tp20 import BusSafetyError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class FoundDTC:
    dtc: DTC
    kind: str  # "Almacenada" / "Pendiente" / "Permanente"
    info: DtcInfo | None  # local knowledge-base entry, when the code is known

    @property
    def title(self) -> str:
        return self.info.title if self.info else "(no está en la base local)"

    @property
    def severity(self) -> str:
        return self.info.severity if self.info else "desconocida"


@dataclass
class ModuleProbe:
    address: str
    name: str
    responded: bool
    frames: list[CapturedFrame] = field(default_factory=list)
    note: str = ""


@dataclass
class ScanResult:
    vin: str | None = None
    protocol: str | None = None
    voltage: str | None = None
    adapter: str | None = None
    capabilities: object | None = None  # AdapterCapabilities, when probed
    dtcs: list[FoundDTC] = field(default_factory=list)
    modules: list[ModuleProbe] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def responding_modules(self) -> list[ModuleProbe]:
        return [m for m in self.modules if m.responded]

    def summary_line(self) -> str:
        n = len(self.dtcs)
        mods = len(self.responding_modules)
        if n == 0:
            return f"Sin fallas almacenadas. {mods} módulo(s) VAG respondieron."
        return f"{n} falla(s) encontrada(s). {mods} módulo(s) VAG respondieron."


class FullScanner:
    """Runs the whole sweep against an already-connected VagscanSession."""

    # (request label, service method name, kind label shown in results)
    _DTC_MODES = [
        ("Leyendo fallas almacenadas", "read_stored_dtcs", "Almacenada"),
        ("Leyendo fallas pendientes", "read_pending_dtcs", "Pendiente"),
        ("Leyendo fallas permanentes", "read_permanent_dtcs", "Permanente"),
    ]

    def __init__(self, session, *, scan_vag_modules: bool = False, module_timeout: float = 0.6):
        """`scan_vag_modules` defaults off on purpose: with it off, a scan is
        pure standard OBD-II - the same requests any commercial scan tool
        makes, nothing non-standard on the bus. Turning it on enables the
        experimental TP2.0 probe, which is gated further by the
        listen-before-transmit interlock in TP20Client."""
        self._session = session
        self._scan_vag_modules = scan_vag_modules
        self._module_timeout = module_timeout

    def total_steps(self) -> int:
        steps = 1 + len(self._DTC_MODES)  # vehicle info + each DTC mode
        if self._scan_vag_modules:
            steps += len(MODULE_ADDRESSES)
        return steps

    def run(self, progress: ProgressCallback | None = None, should_stop: Callable[[], bool] | None = None) -> ScanResult:
        result = ScanResult()
        total = self.total_steps()
        step = 0

        def report(label: str) -> None:
            if progress is not None:
                progress(label, step, total)

        def stopped() -> bool:
            return should_stop is not None and should_stop()

        step += 1
        report("Identificando adaptador y vehículo")
        self._read_vehicle_info(result)

        for label, method_name, kind in self._DTC_MODES:
            if stopped():
                result.warnings.append("Escaneo cancelado por el usuario.")
                return result
            step += 1
            report(label)
            self._read_dtcs(result, method_name, kind)

        if self._scan_vag_modules:
            for module in MODULE_ADDRESSES.values():
                if stopped():
                    result.warnings.append("Escaneo cancelado por el usuario.")
                    return result
                step += 1
                report(f"Consultando módulo {module.address} - {module.name}")
                try:
                    result.modules.append(self._probe_module(module))
                except BusSafetyError as exc:
                    # The interlock's verdict is about this car's bus, not
                    # this module - retrying it 17 times would just print the
                    # same refusal 17 times.
                    result.warnings.append(str(exc))
                    result.modules.clear()
                    break

        report("Escaneo completo")
        return result

    # ------------------------------------------------------------------
    def _read_vehicle_info(self, result: ScanResult) -> None:
        try:
            fingerprint = self._session.driver.identify()
            result.adapter = fingerprint.ati or None
            result.voltage = fingerprint.voltage or None
        except ELM327Error as exc:
            result.warnings.append(f"No se pudo identificar el adaptador: {exc}")
        try:
            protocol = self._session.driver.detected_protocol()
            result.protocol = protocol.name if protocol else None
        except ELM327Error as exc:
            result.warnings.append(f"No se pudo leer el protocolo: {exc}")
        try:
            result.vin = self._session.obd2.read_vin()
        except Exception as exc:  # noqa: BLE001 - VIN is optional info, never fatal to a scan
            result.warnings.append(f"No se pudo leer el VIN: {exc}")
        if self._scan_vag_modules:
            # Only worth the ATMA round trip when the VAG probe is actually
            # being asked for - standard OBD needs none of these commands.
            try:
                capabilities = self._session.ensure_capabilities()
                result.capabilities = capabilities
                if not capabilities.can_do_vag_probing:
                    result.warnings.append(
                        "Este adaptador no implementa "
                        + ", ".join(capabilities.missing())
                        + ". Es típico de los clones baratos. El OBD-II estándar (leer y borrar fallas del "
                        "motor) anda igual; la sonda VAG no se puede usar de forma segura sin modo monitor."
                    )
            except Exception as exc:  # noqa: BLE001 - capability probing is advisory
                result.warnings.append(f"No se pudieron consultar las capacidades del adaptador: {exc}")

    def _read_dtcs(self, result: ScanResult, method_name: str, kind: str) -> None:
        try:
            dtcs = getattr(self._session.obd2, method_name)()
        except Exception as exc:  # noqa: BLE001 - one unsupported mode must not abort the scan
            result.warnings.append(f"{kind}: {exc}")
            return
        for dtc in dtcs:
            matches = self._session.dtc_db.lookup(dtc.code)
            result.dtcs.append(FoundDTC(dtc=dtc, kind=kind, info=matches[0] if matches else None))

    def _probe_module(self, module) -> ModuleProbe:
        try:
            frames = self._session.tp20.discover_channel(module.address, timeout=self._module_timeout)
        except BusSafetyError:
            raise  # the caller stops the whole sweep on this one
        except Exception as exc:  # noqa: BLE001 - a silent module is a normal result, not a failure
            return ModuleProbe(address=module.address, name=module.name, responded=False, note=str(exc))
        if frames:
            return ModuleProbe(address=module.address, name=module.name, responded=True, frames=frames)
        return ModuleProbe(address=module.address, name=module.name, responded=False, note="sin respuesta")
