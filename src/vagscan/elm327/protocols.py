"""ELM327 ATSP protocol numbers (see ELM327 datasheet, "Protocol descriptions").
The Seat Leon Mk2 1.6 BSE (2005-2010, Simos 7.1) is CAN-based and, in
practice, negotiates protocol 6 (ISO 15765-4, CAN 11bit ID, 500 kbaud) for
the OBD-II-compliant part. VAG's own KWP2000-over-CAN (TP2.0) diagnostic
traffic rides the same physical bus/bitrate but is NOT one of the ELM327's
built-in OBD protocols — see vagscan.vag for how we talk to it (raw CAN
pass-through, protocol 6 selected, our own framing on top).
"""
from __future__ import annotations

import enum


class OBDProtocol(enum.Enum):
    AUTO = "0"
    SAE_J1850_PWM = "1"
    SAE_J1850_VPW = "2"
    ISO_9141_2 = "3"
    ISO_14230_4_KWP_5BAUD = "4"
    ISO_14230_4_KWP_FAST = "5"
    ISO_15765_4_CAN_11BIT_500K = "6"
    ISO_15765_4_CAN_29BIT_500K = "7"
    ISO_15765_4_CAN_11BIT_250K = "8"
    ISO_15765_4_CAN_29BIT_250K = "9"
    SAE_J1939_CAN = "A"

    @classmethod
    def from_dpn(cls, code: str) -> "OBDProtocol | None":
        code = code.strip().upper().lstrip("A")  # ATDPN may prefix 'A' for auto-detected
        for proto in cls:
            if proto.value == code:
                return proto
        return None
