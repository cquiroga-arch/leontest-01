"""KWP2000 (ISO 14230-3) diagnostic services, sent over a TP20Channel.

The service IDs (SIDs) used here - 0x10 StartDiagnosticSession, 0x14
ClearDiagnosticInformation, 0x18 ReadDiagnosticTroubleCodesByStatus, 0x27
SecurityAccess, 0x3E TesterPresent - are the ISO 14230-3 *standard* ones,
which is public standard, not manufacturer-secret. VAG's actual ECUs are
known to layer their own proprietary services and session sub-functions on
top of this transport in ways that don't always match the standard 1:1;
where we weren't confident of the exact VAG-specific byte values, we send
the standard-compliant request and surface whatever the ECU actually says
(including a negative response, which at least tells you it understood the
transport and SID framing even if it rejects the sub-function) rather than
guess further. Use vagscan.vag.bus_logger to capture what a real scan tool
sends this ECU and correct any of this that turns out to be wrong for
Simos 7.1 specifically.

Security access (0x27) is implemented only as the seed/key request
mechanism - no seed-to-key algorithm is provided or guessed here. If a
service manual for your own vehicle documents that algorithm for a
legitimate diagnostic function, supply the computed key bytes yourself.
"""
from __future__ import annotations

from dataclasses import dataclass

from .tp20 import TP20Channel, TP20Client, TP20Timeout

__all__ = ["KWP2000Client", "KWP2000Error", "OdometerReading"]

_NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported-invalidFormat",
    0x21: "busy-repeatRequest",
    0x22: "conditionsNotCorrect-requestSequenceError",
    0x31: "requestOutOfRange",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x78: "requestCorrectlyReceived-responsePending",
}


class KWP2000Error(Exception):
    def __init__(self, message: str, nrc: int | None = None):
        super().__init__(message)
        self.nrc = nrc


@dataclass(frozen=True)
class OdometerReading:
    """A mileage value read from the instrument cluster. Read-only: this
    tool has no code path that writes it back, by design."""

    km: int | None  # best-effort decoded value, or None if we couldn't parse it
    raw: bytes  # the raw measuring-block bytes, always kept for inspection
    source_group: int  # which measuring block / identifier it came from

    @property
    def confident(self) -> bool:
        # A plausible odometer for a ~20-year-old car: positive and below a
        # sanity ceiling. Anything outside that is almost certainly the wrong
        # block or a parse that doesn't apply to this cluster.
        return self.km is not None and 0 < self.km < 2_000_000


class KWP2000Client:
    def __init__(self, tp20: TP20Client, channel: TP20Channel):
        self._tp20 = tp20
        self._channel = channel

    def _request(self, sid: int, data: bytes = b"", *, timeout: float = 2.0) -> bytes:
        payload = bytes([sid]) + data
        try:
            reply = self._tp20.send_message(self._channel, payload, timeout=timeout)
        except TP20Timeout as exc:
            raise KWP2000Error(f"no response to SID {sid:02X}: {exc}") from exc
        if not reply:
            raise KWP2000Error(f"empty response to SID {sid:02X}")
        if reply[0] == 0x7F:
            neg_sid = reply[1] if len(reply) > 1 else 0
            code = reply[2] if len(reply) > 2 else 0
            raise KWP2000Error(
                f"negative response to SID {neg_sid:02X}: NRC 0x{code:02X} ({_NRC.get(code, 'unknown')})",
                nrc=code,
            )
        if reply[0] != (sid + 0x40) & 0xFF:
            raise KWP2000Error(f"unexpected response SID 0x{reply[0]:02X}, expected 0x{(sid + 0x40) & 0xFF:02X}")
        return reply[1:]

    def start_diagnostic_session(self, session_type: int = 0x89) -> bytes:
        """SID 0x10. 0x89 ("VAG extended diagnostic session") is the most
        commonly cited sub-function for this kind of access in hobbyist
        write-ups; 0x81 (ISO standard/default session) is the fallback
        worth trying if this is rejected."""
        return self._request(0x10, bytes([session_type]))

    def read_dtc_by_status(self, status_mask: int = 0x00) -> bytes:
        """SID 0x18. Returns the raw DTC record bytes for the caller to
        interpret against vagscan.dtc_db - the exact record layout VAG
        uses isn't guaranteed to match the ISO 14230-3 example layout, so
        this deliberately returns raw bytes rather than a parsed structure."""
        return self._request(0x18, bytes([status_mask]))

    def read_measuring_block(self, group: int) -> bytes:
        """SID 0x21 readDataByLocalIdentifier - VAG's "measuring blocks".
        Purely a read. Returns the raw block bytes for the caller to
        interpret; the layout of each block is cluster-specific."""
        return self._request(0x21, bytes([group]))

    # Instrument-cluster measuring blocks that, across published VAG
    # write-ups for this generation, tend to carry the stored odometer. We
    # try them in order and take the first that decodes to a plausible value
    # - none of this is a spec we can check, hence the sanity filter.
    _MILEAGE_BLOCK_CANDIDATES = (0x22, 0x02, 0x01)

    def read_odometer(self) -> OdometerReading:
        """Read the cluster's stored mileage. READ-ONLY.

        There is deliberately no counterpart that writes it: changing a
        stored odometer is odometer fraud, illegal in essentially every
        jurisdiction, and this project does not implement it. This reads the
        value so it can be seen and documented (e.g. before a legitimate
        cluster replacement), nothing more.
        """
        last_error: KWP2000Error | None = None
        first_raw = b""
        for group in self._MILEAGE_BLOCK_CANDIDATES:
            try:
                raw = self.read_measuring_block(group)
            except KWP2000Error as exc:
                last_error = exc
                continue
            if not first_raw:
                first_raw = raw
            # A readDataByLocalIdentifier response echoes the requested
            # identifier as its first byte; the odometer data follows it.
            # Stripping it keeps that echo byte from being mistaken for part
            # of the value and producing a confident-looking wrong number.
            data = raw[1:] if raw and raw[0] == group else raw
            km = _decode_odometer_km(data)
            reading = OdometerReading(km=km, raw=raw, source_group=group)
            if reading.confident:
                return reading
        if first_raw:
            # Got data but nothing decoded sensibly - hand back the raw bytes
            # rather than a made-up number.
            return OdometerReading(km=None, raw=first_raw, source_group=self._MILEAGE_BLOCK_CANDIDATES[0])
        raise last_error or KWP2000Error("el cuadro no respondió a ninguna lectura de kilometraje")

    def clear_diagnostic_information(self, group_of_dtc: bytes = b"\xff\xff") -> None:
        """SID 0x14. 0xFFFF conventionally means "all groups" in ISO
        14230-3. Caller (the CLI) must have already gotten explicit human
        confirmation - this clears fault memory on whatever module the
        channel is open to, airbag/SRS included if that's the target."""
        self._request(0x14, group_of_dtc)

    def security_access_request_seed(self, level: int, *, i_understand_lockout_risk: bool = False) -> bytes:
        self._check_security_access_opt_in(i_understand_lockout_risk)
        return self._request(0x27, bytes([level]))

    def security_access_send_key(self, level: int, key: bytes, *, i_understand_lockout_risk: bool = False) -> None:
        self._check_security_access_opt_in(i_understand_lockout_risk)
        self._request(0x27, bytes([level + 1]) + key)

    @staticmethod
    def _check_security_access_opt_in(opted_in: bool) -> None:
        """Security access is the one operation here that can leave a module
        worse than it started: a wrong key increments an attempt counter, and
        enough wrong keys put the module into a lockout that needs a timed
        wait (NRC 0x37) or dealer-level tooling to come out of. Nothing in
        the app or CLI calls this - it exists so someone with a documented
        procedure for their own vehicle can use it deliberately, which is
        what the explicit flag is for."""
        if not opted_in:
            raise KWP2000Error(
                "Security access no ejecutado: puede dejar el módulo bloqueado si la clave es incorrecta. "
                "Requiere pasar i_understand_lockout_risk=True desde código propio, con un procedimiento "
                "documentado para tu vehículo. No se invoca desde la app ni desde la CLI."
            )

    def tester_present(self) -> None:
        try:
            self._request(0x3E, timeout=0.5)
        except KWP2000Error:
            pass  # keep-alive purpose only; a rejection here doesn't matter


def _decode_odometer_km(data: bytes) -> int | None:
    """Best-effort decode of a cluster measuring block to kilometres, using
    ONE fixed rule rather than a hunt.

    VAG clusters of this era most commonly store the odometer as a 3-byte
    little-endian kilometre count in the block, so that's what we apply -
    deliberately not a scan across widths and scales, because a scan will
    turn almost any bytes into a plausible-looking number and an odometer
    shown with false confidence is worse than an honest "couldn't decode".
    If this fixed rule gives an implausible value we return None and let the
    caller show the raw bytes instead.
    """
    if len(data) >= 3:
        value = int.from_bytes(data[:3], "little")
        if 0 < value < 2_000_000:
            return value
    return None
