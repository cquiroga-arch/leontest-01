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


def probe_port(device: str, *, baudrate: int = 38400, timeout: float = 1.5) -> ProbeResult:
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
    if any(sig in lowered for sig in _ELM_SIGNATURES):
        return ProbeResult(device=device, responded=True, identity=identity)
    return ProbeResult(device=device, responded=False, identity=identity, error="did not identify as an ELM327")


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


def autodetect_elm327(*, baudrate: int = 38400, timeout: float = 1.5, probe=probe_port) -> ProbeResult | None:
    """Probe every candidate port and return the first that answers as an
    ELM327, or None if nothing did.

    `probe` is injectable so this can be tested without real hardware.
    """
    for device in candidate_ports():
        logger.debug("Probing %s", device)
        result = probe(device, baudrate=baudrate, timeout=timeout)
        if result.responded:
            logger.info("Found ELM327 on %s: %s", result.device, result.identity)
            return result
    return None
