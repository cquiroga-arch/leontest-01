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

from .tp20 import TP20Channel, TP20Client, TP20Timeout

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

    def clear_diagnostic_information(self, group_of_dtc: bytes = b"\xff\xff") -> None:
        """SID 0x14. 0xFFFF conventionally means "all groups" in ISO
        14230-3. Caller (the CLI) must have already gotten explicit human
        confirmation - this clears fault memory on whatever module the
        channel is open to, airbag/SRS included if that's the target."""
        self._request(0x14, group_of_dtc)

    def security_access_request_seed(self, level: int) -> bytes:
        return self._request(0x27, bytes([level]))

    def security_access_send_key(self, level: int, key: bytes) -> None:
        self._request(0x27, bytes([level + 1]) + key)

    def tester_present(self) -> None:
        try:
            self._request(0x3E, timeout=0.5)
        except KWP2000Error:
            pass  # keep-alive purpose only; a rejection here doesn't matter
