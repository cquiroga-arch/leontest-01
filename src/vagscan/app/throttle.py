"""Throttle body alignment (VCDS: 01 - Engine > 04 Basic Settings > group 060)
and idle-speed measurement.

Procedure and preconditions are the ones Ross-Tech documents for drive-by-
wire VAG engines on KWP2000/CAN: ignition ON with the engine OFF, foot off
the accelerator, no engine fault codes, battery >= 11.5 V, coolant between
5 and 95 degC, stay in basic settings ~30 s; the status reads "ADP RUN"
while it learns and "ADP OK" when done. Ross-Tech also notes that some
Simos/Marelli ECUs use a different group (098 or 001), which is why the
group is a parameter rather than hard-wired.

The preconditions are checked over standard OBD-II (works on any adapter);
the alignment itself needs the VAG layer and therefore a monitor-capable
adapter. The engine must be OFF: the ECU moves the throttle plate end to
end while it learns.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Callable

from vagscan.vag.kwp2000 import KWP2000Client, KWP2000Error
from vagscan.vag.measuring import MeasuringValue

ENGINE_ADDRESS = "01"
DEFAULT_TBA_GROUP = 60
MIN_SECONDS = 30.0
MAX_SECONDS = 90.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Preconditions:
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


@dataclass
class IdleStats:
    samples: list[float]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples)

    @property
    def low(self) -> float:
        return min(self.samples)

    @property
    def high(self) -> float:
        return max(self.samples)

    @property
    def swing(self) -> float:
        return self.high - self.low

    @property
    def stdev(self) -> float:
        return statistics.pstdev(self.samples) if len(self.samples) > 1 else 0.0


@dataclass
class AdaptationResult:
    group: int
    status: str  # "ok", "error", "unknown", "cancelled"
    message: str
    readings: list[list[MeasuringValue]] = field(default_factory=list)


def check_preconditions(session) -> Preconditions:
    """Read what we can over standard OBD-II and say what's not met."""
    checks: list[Check] = []
    obd = session.obd2

    try:
        rpm = obd.read_pid("0C")
        checks.append(Check("Motor apagado", rpm < 50, f"{rpm:.0f} rpm" + ("" if rpm < 50 else " - apagá el motor, dejá el contacto")))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Motor apagado", False, f"no se pudo leer RPM ({exc}); poné contacto"))

    try:
        coolant = obd.read_pid("05")
        checks.append(Check("Temperatura refrigerante 5-95 °C", 5 <= coolant <= 95, f"{coolant:.0f} °C"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Temperatura refrigerante 5-95 °C", False, f"no se pudo leer ({exc})"))

    volts = session.driver.read_battery_voltage()
    if volts is None:
        checks.append(Check("Batería >= 11.5 V", False, "el adaptador no informó voltaje"))
    else:
        checks.append(Check("Batería >= 11.5 V", volts >= 11.5, f"{volts:.1f} V"))

    try:
        dtcs = obd.read_stored_dtcs()
        codes = ", ".join(d.code for d in dtcs)
        checks.append(Check("Sin fallas de motor", not dtcs, codes or "ninguna" if not dtcs else codes + " - borralas y apagá/prendé el contacto"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Sin fallas de motor", False, f"no se pudo leer ({exc})"))

    return Preconditions(checks)


def measure_idle(session, seconds: float = 20.0, *, interval: float = 0.25,
                 on_sample: Callable[[float], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> IdleStats:
    """Sample engine RPM (standard OBD-II PID 0C) with the engine idling, to
    put a number on idle hunting before and after the alignment."""
    samples: list[float] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            break
        try:
            rpm = session.obd2.read_pid("0C")
        except Exception:  # noqa: BLE001 - one dropped sample isn't fatal
            rpm = None
        if rpm is not None:
            samples.append(rpm)
            if on_sample:
                on_sample(rpm)
        time.sleep(interval)
    if not samples:
        raise RuntimeError("No se pudo leer ninguna RPM. ¿Motor en marcha y adaptador conectado?")
    return IdleStats(samples)


def _status_text(values: list[MeasuringValue]) -> str:
    return " ".join(v.value for v in values if isinstance(v.value, str)).upper()


def run_throttle_adaptation(session, group: int = DEFAULT_TBA_GROUP, *,
                            on_reading: Callable[[float, list[MeasuringValue]], None] | None = None,
                            should_stop: Callable[[], bool] | None = None,
                            min_seconds: float = MIN_SECONDS, max_seconds: float = MAX_SECONDS,
                            poll_interval: float = 1.0) -> AdaptationResult:
    """Open the engine ECU, enter basic settings for `group`, watch the group
    until the ECU reports OK/ERROR (or max_seconds), then always stop basic
    settings, close the channel and put the adapter back in OBD-II mode."""
    tp20 = session.tp20
    try:
        channel = tp20.discover_and_open(ENGINE_ADDRESS, timeout=1.0)
    except Exception:
        tp20.restore_obd_mode()
        raise
    if channel is None:
        tp20.restore_obd_mode()
        raise RuntimeError(
            "La ECU de motor (módulo 01) no abrió el canal VAG. Con el clon azul es lo esperado (no tiene "
            "modo monitor). Con un adaptador apto, verificá contacto puesto y volvé a intentar."
        )
    kwp = KWP2000Client(tp20, channel)
    result = AdaptationResult(group=group, status="unknown", message="")
    started = False
    try:
        kwp.start_diagnostic_session(0x89)
        try:
            kwp.basic_setting_start(group)
        except KWP2000Error as exc:
            if exc.nrc == 0x31:
                raise RuntimeError(
                    f"La ECU rechazó el grupo {group:03d} (fuera de rango). Algunas Simos usan otro grupo "
                    "para la mariposa (Ross-Tech menciona 098 o 001). No se ejecutó nada."
                ) from exc
            if exc.nrc == 0x22:
                raise RuntimeError(
                    "La ECU dice que no se cumplen las condiciones (NRC 0x22). Revisá: motor apagado, "
                    "contacto puesto, pedal suelto, sin fallas. No se ejecutó nada."
                ) from exc
            raise
        started = True
        t0 = time.monotonic()
        seen_ok = False
        while True:
            elapsed = time.monotonic() - t0
            if should_stop and should_stop():
                result.status, result.message = "cancelled", "Cancelado por el usuario."
                break
            values = kwp.read_group(group)
            result.readings.append(values)
            if on_reading:
                on_reading(elapsed, values)
            text = _status_text(values)
            if "ERR" in text or "FEHLER" in text:
                result.status, result.message = "error", f"La ECU informó error: {text}"
                break
            if "OK" in text:
                seen_ok = True
            if seen_ok and elapsed >= min_seconds:
                result.status, result.message = "ok", f"Adaptación completa: {text}"
                break
            if elapsed >= max_seconds:
                if seen_ok:
                    result.status, result.message = "ok", f"Adaptación completa: {text}"
                else:
                    result.status = "unknown"
                    result.message = (
                        f"Pasaron {max_seconds:.0f} s sin ver 'OK' en el estado (último: {text or 'sin texto'}). "
                        "Revisá los valores leídos; puede que tu ECU informe el estado de otra forma."
                    )
                break
            time.sleep(poll_interval)
    finally:
        if started:
            try:
                kwp.basic_setting_stop(group)
            except Exception:  # noqa: BLE001 - closing the channel ends basic settings too
                pass
        tp20.close_channel(channel)
        tp20.restore_obd_mode()
    return result
