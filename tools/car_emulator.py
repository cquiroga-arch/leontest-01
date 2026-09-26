"""A software ELM327 + car, for testing VAGScan end-to-end without a vehicle.

Opens a pseudo-terminal and speaks the ELM327 command set over it, backed by
a simulated Seat Leon: an engine idling with RPM jitter, coolant warming up,
a running-engine battery voltage (~14.2 V) reported both via ATRV and PID
0142, two stored DTCs, and a VIN. Live values evolve over time so the
gauges move like a real scan tool.

Usage:

    python tools/car_emulator.py               # emulates the cheap clone (default)
    python tools/car_emulator.py --genuine     # emulates an adapter WITH monitor mode

It prints the PTY path it created on the first line. On Linux you can point
VAGScan at it directly, or symlink it so auto-detection finds it:

    ln -sf "$(python tools/car_emulator.py & sleep 1)" /dev/rfcomm0   # (illustrative)

By default it emulates the common blue "ELM327 v1.5 mini": ATMA/ATCAF0/ATCRA
are unimplemented and answer '?', so the VAG/odometer path is declined by
the safety interlock exactly as it is on real clone hardware. Pass --genuine
to emulate a capable adapter that supports monitor mode, which lets the
experimental VAG layer be exercised too.
"""
from __future__ import annotations

import argparse
import math
import os
import pty
import threading
import time

_START = time.time()


class CarEmulator:
    def __init__(self, *, genuine: bool = False):
        self.genuine = genuine
        self.cleared = False
        # This Simos ECU, like the real car, does NOT implement the Mode 01
        # control-module-voltage PID (0142) - the app must fall back to ATRV.
        self.supports_pid_0142 = False
        # The first bus request triggers a protocol search; the adapter emits
        # "SEARCHING..." before the data. We reproduce that once.
        self._searched = False
        # A live powertrain bus, for adapters that can monitor it. 0x200/0x300
        # (the TP2.0 transmit IDs) are deliberately absent so the interlock
        # sees them as free.
        self.bus_traffic = "\r".join(
            [
                "280 49 0E 00 00 00 00 00 1A",
                "288 00 00 00 00 00 00 00 00",
                "320 05 00 00 00 00 00 00 00",
                "420 00 00 00 00 00 00 00 00",
            ]
        )

    @property
    def _unimplemented(self) -> set[str]:
        if self.genuine:
            return {"AT@1"}
        return {"ATMA", "ATCAF0", "ATCRA", "ATCRA7E8", "AT@1"}

    def battery_voltage(self) -> str:
        return f"{14.2 + 0.05 * math.sin((time.time() - _START) * 3.0):.1f}V"

    def _live(self, cmd: str) -> str:
        t = time.time() - _START
        if cmd == "010C":
            rpm = int(850 + 60 * math.sin(t * 1.7) + 25 * math.sin(t * 5.3))
            return f"41 0C {(rpm * 4) >> 8:02X} {(rpm * 4) & 0xFF:02X}"
        if cmd == "010D":
            return "41 0D 00"
        if cmd == "0105":
            return f"41 05 {int(min(90, 60 + t * 0.75)) + 40:02X}"
        if cmd == "0111":
            return f"41 11 {int((14 + 2 * math.sin(t * 2.1)) * 255 / 100):02X}"
        if cmd == "010F":
            return f"41 0F {28 + 40:02X}"
        if cmd == "0142":
            if not self.supports_pid_0142:
                return "NO DATA"  # ECU doesn't implement it - app uses ATRV
            mv = int(14200 + 60 * math.sin(t * 3.0))
            return f"41 42 {mv >> 8:02X} {mv & 0xFF:02X}"
        return "NO DATA"

    def handle(self, cmd: str) -> str:
        if cmd in self._unimplemented:
            return "?"
        if cmd == "ATI":
            return "ELM327 v1.5"
        if cmd == "AT@2":
            return "?"
        if cmd == "ATRV":
            return self.battery_voltage()
        if cmd == "ATDPN":
            return "A6"
        if cmd == "ATMA":  # only reached when genuine (else '?')
            return self.bus_traffic
        if cmd == "03":
            return "NO DATA" if self.cleared else "43 03 00 01 71"
        if cmd in ("07", "0A"):
            return "NO DATA"
        if cmd == "04":
            self.cleared = True
            return "44"
        if cmd.startswith("01") or cmd.startswith("09"):
            # First real bus request negotiates a protocol: answer once with
            # SEARCHING... before the data, like a real adapter on a cold link.
            prefix = ""
            if not self._searched:
                self._searched = True
                prefix = "SEARCHING...\r"
            if cmd.startswith("09"):
                return prefix + "49 02 01 57 56 57 5A 5A 5A 31 4B 5A 38 57 30 30 30 30 30 31"
            return prefix + self._live(cmd)
        if cmd.endswith("C0FFFFFFFFFFFF"):
            return "NO DATA"  # no module answers the TP2.0 probe in this sim
        return "OK"


def serve(emu: CarEmulator) -> None:
    master, slave = pty.openpty()
    print(os.ttyname(slave), flush=True)
    buf = b""
    while True:
        try:
            chunk = os.read(master, 256)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        while b"\r" in buf:
            line, buf = buf.split(b"\r", 1)
            reply = emu.handle(line.decode(errors="replace").strip())
            os.write(master, (reply + "\r>").encode())


def main() -> None:
    parser = argparse.ArgumentParser(description="Software ELM327 + car for testing VAGScan.")
    parser.add_argument("--genuine", action="store_true", help="emulate an adapter with working monitor mode")
    parser.add_argument("--seconds", type=float, default=1800, help="how long to stay alive")
    args = parser.parse_args()
    emu = CarEmulator(genuine=args.genuine)
    threading.Thread(target=serve, args=(emu,), daemon=True).start()
    time.sleep(args.seconds)


if __name__ == "__main__":
    main()
