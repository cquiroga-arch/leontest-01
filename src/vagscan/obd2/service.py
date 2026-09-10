"""SAE J1979 generic OBD-II service layer, built on top of ELM327Driver.

Works against any OBD-II-compliant vehicle (not VAG-specific). See
vagscan.vag for the manufacturer-specific layer that reaches beyond what
Mode 03/07/0A can show (e.g. the airbag/SRS module, which is not part of
the federally-mandated OBD-II DTC modes at all).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from vagscan.elm327.driver import ELM327Driver, ELM327Error

from .pids import PID_TABLE

_HEX_BYTE = re.compile(r"[0-9A-Fa-f]{2}")

_DTC_LETTER = ("P", "C", "B", "U")


class ObdRequestError(Exception):
    """A Mode/PID request failed or returned unparseable data."""


@dataclass(frozen=True)
class DTC:
    code: str  # e.g. "P0301"
    raw: str  # original 2-byte hex, for debugging/db lookup fallback

    def __str__(self) -> str:
        return self.code


def _extract_bytes(response: str, expect_mode_ack: str) -> list[int]:
    """The ELM327, in CAN auto-formatting mode, hands back one logical
    response per line with the mode-ack and (for Mode 01/09) PID echoed
    back as the first bytes. Multi-frame CAN responses are already
    reassembled by the adapter, so we just need to strip the ack/PID and
    parse the rest as hex bytes.
    """
    lines = [ln for ln in response.splitlines() if ln.strip()]
    if not lines:
        raise ObdRequestError("empty response")
    # Multi-ECU responses (e.g. two modules both answering Mode 03) show up
    # as multiple lines; we only care about data, not which module answered,
    # for generic OBD-II purposes, so bytes from every line are pooled.
    all_bytes: list[int] = []
    for line in lines:
        # Drop a leading CAN header if headers happen to be on (ATH1),
        # e.g. "7E8 06 43 01 33 00 00" -> keep from the mode-ack onward.
        tokens = _HEX_BYTE.findall(line)
        if not tokens:
            continue
        byte_vals = [int(t, 16) for t in tokens]
        try:
            ack_index = byte_vals.index(int(expect_mode_ack, 16))
        except ValueError:
            continue
        all_bytes.extend(byte_vals[ack_index + 1 :])
    if not all_bytes:
        raise ObdRequestError(f"no mode-ack {expect_mode_ack} found in {response!r}")
    return all_bytes


def decode_dtcs(payload: list[int]) -> list[DTC]:
    dtcs: list[DTC] = []
    for i in range(0, len(payload) - 1, 2):
        b0, b1 = payload[i], payload[i + 1]
        if b0 == 0 and b1 == 0:
            continue  # padding
        letter = _DTC_LETTER[(b0 & 0xC0) >> 6]
        digit1 = (b0 & 0x30) >> 4
        digit2 = b0 & 0x0F
        digit3 = (b1 & 0xF0) >> 4
        digit4 = b1 & 0x0F
        code = f"{letter}{digit1}{digit2}{digit3}{digit4}"
        dtcs.append(DTC(code=code, raw=f"{b0:02X}{b1:02X}"))
    return dtcs


class OBD2Service:
    def __init__(self, driver: ELM327Driver):
        self._driver = driver

    # -- Mode 01: live data ------------------------------------------------
    def read_pid(self, pid: str) -> float:
        pid = pid.upper()
        pid_def = PID_TABLE.get(pid)
        if pid_def is None:
            raise ObdRequestError(f"unknown PID {pid}")
        response = self._driver.send_command(f"01{pid}")
        payload = _extract_bytes(response, expect_mode_ack="41")
        # First payload byte after the ack is the echoed PID itself.
        if not payload or f"{payload[0]:02X}" != pid:
            raise ObdRequestError(f"PID echo mismatch reading {pid}: {response!r}")
        data = bytes(payload[1:])
        if len(data) < pid_def.min_bytes:
            raise ObdRequestError(f"short response reading {pid}: {response!r}")
        return pid_def.decode(data)

    def read_pids(self, pids: list[str]) -> dict[str, float]:
        out = {}
        for pid in pids:
            try:
                out[pid] = self.read_pid(pid)
            except (ObdRequestError, ELM327Error) as exc:
                out[pid] = None  # module didn't support this PID; surface as unavailable
        return out

    # -- Mode 03/07/0A: DTCs ------------------------------------------------
    def read_stored_dtcs(self) -> list[DTC]:
        response = self._driver.send_command("03")
        return decode_dtcs(_extract_bytes(response, expect_mode_ack="43"))

    def read_pending_dtcs(self) -> list[DTC]:
        response = self._driver.send_command("07")
        return decode_dtcs(_extract_bytes(response, expect_mode_ack="47"))

    def read_permanent_dtcs(self) -> list[DTC]:
        response = self._driver.send_command("0A")
        return decode_dtcs(_extract_bytes(response, expect_mode_ack="4A"))

    # -- Mode 04: clear DTCs + reset readiness monitors --------------------
    def clear_dtcs(self) -> None:
        """Mode 04. Clears stored/pending DTCs, the MIL, and freeze frame,
        and resets all readiness monitors — the ECU won't report emissions
        readiness again until a full drive cycle completes. Caller (the CLI)
        is expected to have already gotten explicit human confirmation.
        """
        self._driver.send_command("04")

    # -- Mode 09: vehicle info ----------------------------------------------
    def read_vin(self) -> str | None:
        try:
            response = self._driver.send_command("0902")
        except ELM327Error:
            return None
        payload = _extract_bytes(response, expect_mode_ack="49")
        if not payload or payload[0] != 0x02:
            return None
        data = payload[1:]
        if data and data[0] in (0x01, 0x00):
            data = data[1:]  # leading "number of data items" byte some adapters include
        text = bytes(b for b in data if 32 <= b < 127).decode("ascii", errors="ignore")
        return text or None
