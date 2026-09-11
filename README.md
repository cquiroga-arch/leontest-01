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
- **Desktop GUI (`vagscan.gui`)**: the friendly front end and what most
  people should run. Auto-detects the adapter, one **ESCANEAR** button that
  sweeps the whole car, results with descriptions/causes/checks, live
  gauges, and clearing that acts on the real ECU behind a
  confirmation-phrase gate - see below.
- **Adapter auto-detection (`vagscan.transport.discovery`)**: probes each
  serial port (including `/dev/rfcomm*`, which some distros don't
  enumerate) and identifies the one that answers as an ELM327, instead of
  making you guess which COM port it landed on.
- **Full scan (`vagscan.app.full_scan`)**: one pass over generic OBD-II
  plus every known VAG module address, reporting partial results - a silent
  module or an unsupported mode is a normal outcome, not an aborted scan.
- **CLI (`vagscan.app.cli`)**: the same functionality as a scriptable
  terminal tool - `ports`, `identify`, `live`, `dtc-read`, `dtc-clear`,
  `vin`, `vag-modules`, `vag-discover`, `vag-log`, `vag-clear`.

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

## What this actually puts on the car's bus

Audited by tracing every code path that can reach the vehicle, and verified
against an emulator by logging the wire. `AT*` commands configure the
adapter locally and never reach the bus; only these five things do:

| Traffic | Kind | Risk |
|---|---|---|
| `01xx` (Mode 01) | standard read | none - what every scan tool sends |
| `03` / `07` / `0A` | standard read | none |
| `0902` (VIN) | standard read | none |
| `04` (clear DTCs) | standard **write** | reversible; resets MIL + readiness monitors. Gated behind a typed confirmation phrase |
| TP2.0 probe frame | **non-standard** | the only real question mark - see below |

A default scan is **only** the standard reads. The VAG module sweep is a
checkbox that starts unchecked.

Volume is a non-issue: a full module sweep is one 8-byte frame per module,
~17 frames spread over ~17 seconds, against a bus that normally carries
thousands of frames per second. There is no path in this code that can
flood the bus.

**Listen before transmit.** The TP2.0 probe transmits on CAN ID 0x200
because that is what public reverse-engineering says the diagnostic
channel-setup broadcast is - not something anyone here can verify against a
spec. If that's wrong for a given car and 0x200 actually carries a module's
real traffic, transmitting would put a second sender on an ID a real
receiver is consuming. So `TP20Client` passively monitors the bus first and
**refuses to transmit** on an ID already in use, aborting the sweep with one
explanation. Verified against a simulated car with 0x200 occupied: zero
probe frames went out.

**Security access (KWP2000 service 0x27) is the one operation here that
could leave a module worse than it started** - wrong keys increment a
lockout counter. Nothing in the app or the CLI calls it, and it now refuses
to run without an explicit `i_understand_lockout_risk=True` from your own
code. There is no flashing, no coding, no adaptation writing, and no
immobilizer code anywhere in this project.

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

## How you actually use it

1. Plug the ELM327 into the car's OBD port, ignition on.
2. Pair it once in the OS's Bluetooth settings (it becomes a COM port /
   `/dev/rfcommN`).
3. Open the app. It probes the serial ports on startup and connects to
   whichever one answers like an ELM327 - you don't pick a port by hand
   (there's a "Buscar adaptador" button to redo it, and the dropdown is
   still there as a manual override).
4. Hit **ESCANEAR**. It reads the engine over generic OBD-II
   (stored/pending/permanent codes + VIN) and then asks each known VAG
   module address whether it's there, with a progress bar the whole way.
5. Results come back in one tree: every code with its description, likely
   causes and what to check, plus which modules answered.
6. **Borrar las fallas encontradas** clears them on the real ECU (OBD-II
   Mode 04) after you type the confirmation phrase, then re-scans
   automatically so you can see what actually cleared and what came back.

## Screenshots (GUI)

| Escaneo completo | Confirmación antes de borrar |
|---|---|
| ![Scan](docs/screenshots/scan.png) | ![Clear](docs/screenshots/clear-confirm.png) |

| Después de borrar | En vivo |
|---|---|
| ![After clear](docs/screenshots/after-clear.png) | ![Live](docs/screenshots/live.png) |

| Fallas (detalle) | VAG avanzado |
|---|---|
| ![DTC](docs/screenshots/dtc.png) | ![VAG](docs/screenshots/vag.png) |

These were taken against a scripted ELM327 emulator (no car needed to see
the UI work) - see the "Tests" section below for how that emulator works.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

Tkinter (the GUI toolkit) ships with the official python.org installers for
Windows/macOS, so most people already have it. On Linux, if `vagscan-gui`
complains it's missing, install your distro's package first (Debian/Ubuntu:
`sudo apt install python3-tk`).

Pair the ELM327 at the OS level first (Windows Settings > Bluetooth &
devices, or `bluetoothctl` on Linux) - it'll show up as a serial/COM port.
Then either:

```bash
vagscan-gui                       # the desktop app - pick the port from the dropdown and hit Conectar
```

or, for scripting/automation, the CLI:

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

55 tests run against an in-memory fake transport (`tests/conftest.py`) - no
hardware required. They validate our own protocol/framing logic (driver AT
sequencing, OBD2 PID/DTC decoding, TP2.0 single-frame send/receive, KWP2000
positive/negative response handling, the GUI's background IOWorker), not
the real ECU's behavior, which can only be confirmed against the actual
car. The GUI itself was additionally driven end-to-end (connect, read
DTCs, stream live data, navigate every tab) against a scripted fake
ELM327 under a virtual display, which is how the screenshots above were
produced.
