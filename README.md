# vagscan

OBD-II + VAG (KWP2000/TP2.0) diagnostic scanner over an ELM327 Bluetooth
adapter, built and tuned for a **Seat Leon Mk2 (1P) 1.6 BSE** (Simos 7.1
ECU) but the generic OBD-II layer works on any OBD-II-compliant vehicle.

Connects from a PC over a paired Bluetooth serial port (shows up as a COM
port on Windows, `/dev/rfcomm*` on Linux once paired at the OS level). A
mobile front-end can reuse this same package later - it was split into
transport / driver / service layers specifically so the protocol logic
isn't tied to the CLI.

## What actually works today

- **Generic OBD-II (`vagscan.obd2`)**: Mode 01 live data (RPM, speed,
  coolant/intake temp, fuel trims, MAF, throttle, etc.), Mode 03/07/0A
  (stored/pending/permanent DTCs), Mode 04 (clear DTCs), Mode 09 (VIN).
  This is standard, well-tested, works on this car and any other OBD-II
  vehicle.
- **DTC knowledge base (`vagscan.dtc_db`)**: generic SAE J2012 code
  descriptions/causes/checks, plus a small set of VAG-specific *guidance*
  entries (airbag/SRS, ABS, instrument cluster, comfort system). See the
  `_meta` note in `dtc_db/data/vag_codes.json` - the VAG entries are
  engineering guidance, not a recitation of VAG's internal fault-code
  catalog, and are meant to be filled in with confirmed codes as you scan
  this specific car.
- **CLI (`vagscan.app.cli`)**: `ports`, `identify`, `live`, `dtc-read`,
  `dtc-clear`, `vin`, `vag-modules`, `vag-discover`, `vag-log`, `vag-clear`.

## What's experimental (`vagscan.vag`) and why

Simos 7.1 speaks VAG's proprietary KWP2000-over-CAN transport (TP2.0) for
anything beyond the OBD-II-mandated part of its behavior, and the ELM327
chipset has no built-in support for it - it only implements the standard
ISO 15765-4 OBD transport. `vagscan.vag` implements TP2.0 channel setup and
KWP2000 messaging on top of the ELM327's raw-CAN pass-through mode
(`ATSH`/`ATCAF0`/`ATMA`), because that's the only way to reach it at all
with this hardware.

**The exact byte-level details (channel-setup frame layout, resulting CAN
IDs, multi-frame segmentation) are an informed best guess from public
hobbyist reverse-engineering write-ups, not a spec anyone here can check
against.** `vag-discover` and `vag-log` exist specifically to validate (or
correct) those constants against *this* car before you trust anything
beyond read-only use - see the docstrings in `tp20.py` and `kwp2000.py` for
exactly what's uncertain and where to fix it once you have a real capture.

Out of scope, deliberately: no immobilizer bypass, no security-access
seed-to-key algorithm. `security_access_*` in `kwp2000.py` is a bare
request/response mechanism you can plug a manufacturer-documented
procedure into for your own vehicle - nothing is hardcoded or guessed here.

## Safety notes

- Clearing a DTC (generic `dtc-clear` or `vag-clear`) resets the MIL and
  readiness monitors; the car reports emissions "not ready" until a full
  drive cycle completes. Both commands require typing an exact
  confirmation phrase - there's no `--yes` flag.
- Clearing the **airbag/SRS module (address 15)** requires typing a
  separate, longer confirmation phrase. Clearing a fault code there does
  not fix whatever tripped it; if the underlying issue (e.g. a squib
  circuit fault) is still present, the airbag system may not deploy
  correctly in a real collision. Never probe a pyrotechnic squib circuit
  with a generic multimeter.
- The final call on any reading is yours (the human at the keyboard) - this
  tool surfaces data, it doesn't make diagnostic decisions.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

Pair the ELM327 at the OS level first (Windows Settings > Bluetooth &
devices, or `bluetoothctl` on Linux), then:

```bash
vagscan ports                     # find the paired adapter's serial port
vagscan --port COM5 identify      # or /dev/rfcomm0 on Linux
vagscan --port COM5 live --pids 0C,0D,05
vagscan --port COM5 dtc-read --pending --permanent
vagscan --port COM5 vag-modules
vagscan --port COM5 vag-log --seconds 30 --out capture.log
```

## Tests

```bash
pytest
```

All 35+ tests run against an in-memory fake transport (`tests/conftest.py`)
- no hardware required. They validate our own protocol/framing logic
(driver AT sequencing, OBD2 PID/DTC decoding, TP2.0 single-frame
send/receive, KWP2000 positive/negative response handling), not the real
ECU's behavior, which can only be confirmed against the actual car.
