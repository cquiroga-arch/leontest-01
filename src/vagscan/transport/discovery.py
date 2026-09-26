"""Finding the ELM327 without making the user pick a port by hand.

Pairing itself is still the OS Bluetooth stack's job (Windows Settings >
Bluetooth & devices, or `bluetoothctl` on Linux) - once paired, a classic
SPP adapter shows up as an ordinary serial port. What we do here is figure
out *which* of the host's serial ports is actually the adapter, by talking
to each candidate and seeing which one answers like an ELM327, rather than
trusting the port description (which on Windows is usually just
"Standard Serial over Bluetooth link" for every paired device).
"""
from __future__ import annotations

import glob
import logging
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

logger = logging.getLogger(__name__)

# Name hints are only used to decide which port to *probe first* - they make
# detection faster when the description happens to be informative, and cost
# nothing when it isn't.
_ELM_HINTS = ("elm327", "elm 327", "obdii", "obd2", "obd-ii", "vlink", "obdlink", "vgate", "konnwei", "viecar")

# An ELM327 (genuine or clone) answers ATI with something containing "ELM327";
# some clones answer with their own branding, so we also accept anything that
# ends in the ELM327 prompt character with a plausible version-ish reply.
_ELM_SIGNATURES = ("elm327", "elm 327", "obdii", "obdlink", "stn")


@dataclass
class PortInfo:
    device: str
    description: str
    hwid: str
    likely_elm327: bool


@dataclass
class ProbeResult:
    device: str
    responded: bool
    identity: str  # raw ATI reply, when we got one
    error: str = ""
    baudrate: int = 38400  # the baud rate the adapter actually answered at
    saw_data: bool = False  # did the port return *any* bytes (vs. dead silence)


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
    """Name-based guess only - no I/O. `autodetect_elm327` is the real one."""
    candidates = [p for p in list_serial_ports() if p.likely_elm327]
    if len(candidates) == 1:
        return candidates[0].device
    return None


def probe_port(device: str, *, baudrate: int = 38400, timeout: float = 1.0) -> ProbeResult:
    """Open one serial port and ask it who it is (ATI). Never raises: a port
    that's busy, permission-denied, or simply isn't an adapter comes back as
    `responded=False` with the reason, because probing is expected to hit
    ports that belong to something else entirely."""
    try:
        with serial.Serial(port=device, baudrate=baudrate, timeout=timeout, write_timeout=timeout) as port:
            # ATZ resets and flushes whatever state a previous session left;
            # some clones need it before they answer anything coherently.
            port.write(b"ATZ\r")
            port.flush()
            _read_until_prompt(port, timeout)
            port.write(b"ATI\r")
            port.flush()
            reply = _read_until_prompt(port, timeout)
    except (serial.SerialException, OSError, ValueError) as exc:
        return ProbeResult(device=device, responded=False, identity="", error=str(exc))

    text = reply.decode("ascii", errors="replace")
    lowered = text.lower()
    identity = " ".join(line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip() and line.strip() != ">")
    saw = bool(reply)
    if any(sig in lowered for sig in _ELM_SIGNATURES):
        return ProbeResult(device=device, responded=True, identity=identity, baudrate=baudrate, saw_data=saw)
    return ProbeResult(device=device, responded=False, identity=identity, error="did not identify as an ELM327", saw_data=saw)


def _read_until_prompt(port: serial.Serial, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = port.read(1)
        if not chunk:
            continue
        buf.extend(chunk)
        if buf.endswith(b">"):
            break
    return bytes(buf)


# Baud rates to try, most likely first. Bluetooth SPP clones are all over
# the place here: the blue "v1.5 mini" usually enumerates at 38400, but a
# good number default to 9600, and some to 115200. Probing 38400 only (as we
# used to) means a 9600 clone pairs fine, gets a COM port, and still never
# gets recognised - which looks exactly like "it doesn't appear".
_BAUD_CANDIDATES = (38400, 9600, 115200, 500000)


def candidate_ports() -> list[str]:
    """Every device worth probing, most-likely first.

    Starts from the enumerated serial ports, then adds any `/dev/rfcomm*`
    that exist on disk: those are created by `rfcomm bind` on Linux for a
    paired Bluetooth SPP device, and depending on the distro they don't
    always show up in the normal port enumeration - which would otherwise
    make the adapter invisible to us on exactly the setup this tool targets.
    """
    ports = list_serial_ports()
    ports.sort(key=lambda p: not p.likely_elm327)
    devices = [p.device for p in ports]
    for path in sorted(glob.glob("/dev/rfcomm*")):
        if path not in devices:
            devices.append(path)
    return devices


def autodetect_elm327(
    *, baudrates: tuple[int, ...] = _BAUD_CANDIDATES, timeout: float = 1.5, probe=probe_port
) -> ProbeResult | None:
    """Probe every candidate port, at each candidate baud rate, and return the
    first that answers as an ELM327, or None if nothing did.

    `probe` is injectable so this can be tested without real hardware.
    """
    for device in candidate_ports():
        for i, baud in enumerate(baudrates):
            logger.debug("Probing %s @ %d", device, baud)
            result = probe(device, baudrate=baud, timeout=timeout)
            if result.responded:
                result.baudrate = baud
                logger.info("Found ELM327 on %s @ %d: %s", result.device, baud, result.identity)
                return result
            # A port that returned nothing at all at the first baud is dead,
            # unpowered, or not a serial device - trying the other baud rates
            # on it just wastes a timeout each. Only keep trying alternate
            # rates when the port actually said *something* (which is what a
            # wrong-baud ELM327 does: garbage, not silence).
            if i == 0 and not result.saw_data:
                break
    return None
