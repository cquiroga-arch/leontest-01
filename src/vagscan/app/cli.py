"""Command-line front-end. Every command that changes the car's state
(clearing DTCs, generic or VAG-specific) requires the human at the keyboard
to type an exact confirmation phrase - per the project's own stated
criterion, this tool surfaces everything it can read and leaves the final
call to a human, never a --yes flag or a default-on prompt.
"""
from __future__ import annotations

import argparse
import sys
import time

from rich.console import Console
from rich.table import Table

from vagscan.dtc_db import DtcDatabase
from vagscan.elm327 import OBDProtocol
from vagscan.transport import guess_elm327_port, list_serial_ports
from vagscan.vag.addresses import MODULE_ADDRESSES

from .session import VagscanSession

console = Console()


def _confirm(prompt: str, required_phrase: str) -> bool:
    console.print(f"[bold yellow]{prompt}[/bold yellow]")
    typed = input(f'Type "{required_phrase}" to proceed, anything else to cancel: ')
    return typed.strip() == required_phrase


def _resolve_port(args) -> str:
    if args.port:
        return args.port
    guessed = guess_elm327_port()
    if guessed:
        console.print(f"[green]Auto-detected adapter on {guessed}[/green]")
        return guessed
    console.print("[red]Could not auto-detect the ELM327's serial port.[/red]")
    for p in list_serial_ports():
        console.print(f"  {p.device}: {p.description}")
    console.print("Pass --port explicitly (COM5, /dev/rfcomm0, ...).")
    raise SystemExit(2)


def cmd_ports(args) -> None:
    table = Table(title="Serial ports")
    table.add_column("Device")
    table.add_column("Description")
    table.add_column("Looks like ELM327?")
    for p in list_serial_ports():
        table.add_row(p.device, p.description, "yes" if p.likely_elm327 else "")
    console.print(table)


def cmd_identify(args) -> None:
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        fp = session.driver.identify()
        proto = session.driver.detected_protocol()
        table = Table(title="ELM327 identification")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("ATI (version string)", fp.ati)
        table.add_row("AT@1 (device desc)", fp.at_device_desc)
        table.add_row("AT@2 (device id)", fp.at_device_id)
        table.add_row("Battery voltage (ATRV)", fp.voltage)
        table.add_row("Negotiated protocol", proto.name if proto else "unknown")
        console.print(table)
        console.print(
            "[dim]Note: a clone can report a plausible-looking ATI string on purpose - "
            "this identifies what the adapter *says* it is, not a guarantee.[/dim]"
        )


def cmd_live(args) -> None:
    pids = [p.strip().upper() for p in args.pids.split(",")]
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        try:
            while True:
                values = session.obd2.read_pids(pids)
                table = Table(title="Live data")
                table.add_column("PID")
                table.add_column("Value")
                for pid, value in values.items():
                    table.add_row(pid, "unsupported" if value is None else str(value))
                console.clear()
                console.print(table)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass


def _print_dtcs(title: str, dtcs, db: DtcDatabase) -> None:
    table = Table(title=title)
    table.add_column("Code")
    table.add_column("Title")
    table.add_column("Severity")
    if not dtcs:
        console.print(f"[green]{title}: none stored.[/green]")
        return
    for dtc in dtcs:
        matches = db.lookup(dtc.code)
        if matches:
            table.add_row(dtc.code, matches[0].title, matches[0].severity)
        else:
            table.add_row(dtc.code, "(not in local database)", "unknown")
    console.print(table)
    for dtc in dtcs:
        for info in db.lookup(dtc.code):
            console.print(f"\n[bold]{dtc.code}[/bold] - {info.title} ([italic]{info.severity}[/italic])")
            console.print(info.description)
            if info.common_causes:
                console.print("  Common causes: " + "; ".join(info.common_causes))
            if info.suggested_checks:
                console.print("  Suggested checks: " + "; ".join(info.suggested_checks))


def cmd_dtc_read(args) -> None:
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        _print_dtcs("Stored DTCs (Mode 03)", session.obd2.read_stored_dtcs(), session.dtc_db)
        if args.pending:
            _print_dtcs("Pending DTCs (Mode 07)", session.obd2.read_pending_dtcs(), session.dtc_db)
        if args.permanent:
            _print_dtcs("Permanent DTCs (Mode 0A)", session.obd2.read_permanent_dtcs(), session.dtc_db)


def cmd_dtc_clear(args) -> None:
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        current = session.obd2.read_stored_dtcs()
        _print_dtcs("Stored DTCs about to be cleared", current, session.dtc_db)
        if not current and not args.force:
            console.print("[green]Nothing stored - not clearing anything. Use --force to clear anyway.[/green]")
            return
        console.print(
            "[bold red]This resets the MIL and every readiness monitor - the car will report "
            "\"not ready\" for emissions until a full drive cycle completes.[/bold red]"
        )
        if _confirm("Clear generic OBD-II DTCs now?", "BORRAR"):
            session.obd2.clear_dtcs()
            console.print("[green]Cleared.[/green]")
        else:
            console.print("Cancelled.")


def cmd_vin(args) -> None:
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        vin = session.obd2.read_vin()
        console.print(f"VIN: {vin or '(not returned by this ECU)'}")


def cmd_vag_modules(args) -> None:
    table = Table(title="Known VAG module addresses")
    table.add_column("Address")
    table.add_column("Name")
    table.add_column("Notes")
    for m in MODULE_ADDRESSES.values():
        table.add_row(m.address, m.name, m.notes)
    console.print(table)


def cmd_vag_discover(args) -> None:
    console.print(
        "[yellow]Experimental: sends a best-guess TP2.0 channel-setup probe and shows every "
        "raw frame that comes back, unvalidated. See vagscan/vag/tp20.py for why.[/yellow]"
    )
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        frames = session.tp20.discover_channel(args.module, timeout=args.timeout)
        if not frames:
            console.print(f"[red]No reply from module {args.module}.[/red]")
            return
        table = Table(title=f"Candidate replies for module {args.module}")
        table.add_column("CAN ID")
        table.add_column("Data (hex)")
        for f in frames:
            table.add_row(f.can_id_hex, f.data.hex().upper())
        console.print(table)
        console.print("Pick the real tx/rx pair by comparing against a known-working tool's capture if you have one.")


def cmd_vag_log(args) -> None:
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        console.print(f"Capturing raw bus traffic for {args.seconds}s...")
        frames = session.bus_logger.capture_for(args.seconds)
        console.print(f"Captured {len(frames)} frames.")
        if args.out:
            session.bus_logger.save(args.out)
            console.print(f"Appended to {args.out}")
        else:
            for line in session.bus_logger.to_log_lines()[-50:]:
                console.print(line)


def cmd_vag_clear(args) -> None:
    module = MODULE_ADDRESSES.get(args.module.upper())
    name = module.name if module else args.module
    console.print(f"[yellow]Experimental path: opening TP2.0 channel to module {args.module} ({name}).[/yellow]")
    with VagscanSession.connect(_resolve_port(args), baudrate=args.baudrate) as session:
        frames = session.tp20.discover_channel(args.module, timeout=args.timeout)
        if not frames:
            console.print(f"[red]No reply from module {args.module} - cannot proceed.[/red]")
            return
        console.print("Candidate replies:")
        for f in frames:
            console.print(f"  {f.can_id_hex}: {f.data.hex().upper()}")
        if not args.rx_id or not args.tx_id:
            console.print(
                "[red]Pass --tx-id/--rx-id (hex) once you've identified the real channel from the "
                "candidates above - this is deliberately not automatic.[/red]"
            )
            return
        channel = session.tp20.open_channel(
            args.module, int(args.tx_id, 16), int(args.rx_id, 16)
        )
        from vagscan.vag.kwp2000 import KWP2000Client, KWP2000Error

        kwp = KWP2000Client(session.tp20, channel)
        if args.module.upper() == "15":
            console.print(
                "[bold red]This is the airbag/SRS module. Clearing its fault memory does not fix "
                "whatever tripped it - a real fault (e.g. a squib circuit issue) will just come back, "
                "and if you clear a code for a fault that's still physically present the airbag system "
                "may not deploy correctly in a real collision. Only clear this after the underlying "
                "fault is actually repaired.[/bold red]"
            )
            phrase = "ENTIENDO EL RIESGO"
        else:
            phrase = "BORRAR"
        if not _confirm(f"Clear fault memory on module {args.module} ({name})?", phrase):
            console.print("Cancelled.")
            return
        try:
            kwp.clear_diagnostic_information()
            console.print("[green]Clear command sent.[/green]")
        except KWP2000Error as exc:
            console.print(f"[red]ECU rejected the clear request: {exc}[/red]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vagscan")
    parser.add_argument("--port", help="Serial port (COM5, /dev/rfcomm0, ...). Auto-detected if omitted.")
    parser.add_argument("--baudrate", type=int, default=38400)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="List serial ports").set_defaults(func=cmd_ports)
    sub.add_parser("identify", help="Show ELM327 fingerprint/protocol/voltage").set_defaults(func=cmd_identify)

    p_live = sub.add_parser("live", help="Stream Mode 01 live data")
    p_live.add_argument("--pids", default="0C,0D,05,11", help="Comma-separated PIDs, e.g. 0C,0D,05")
    p_live.add_argument("--interval", type=float, default=0.5)
    p_live.set_defaults(func=cmd_live)

    p_dtc = sub.add_parser("dtc-read", help="Read DTCs (generic OBD-II)")
    p_dtc.add_argument("--pending", action="store_true")
    p_dtc.add_argument("--permanent", action="store_true")
    p_dtc.set_defaults(func=cmd_dtc_read)

    p_clear = sub.add_parser("dtc-clear", help="Clear DTCs (generic OBD-II, Mode 04)")
    p_clear.add_argument("--force", action="store_true", help="Send the clear even if nothing is currently stored")
    p_clear.set_defaults(func=cmd_dtc_clear)

    sub.add_parser("vin", help="Read VIN (Mode 09 PID 02)").set_defaults(func=cmd_vin)
    sub.add_parser("vag-modules", help="List known VAG module addresses").set_defaults(func=cmd_vag_modules)

    p_disc = sub.add_parser("vag-discover", help="Experimental: probe a VAG module's TP2.0 channel")
    p_disc.add_argument("module", help="Two-hex-digit module address, e.g. 01 (engine) or 15 (airbag)")
    p_disc.add_argument("--timeout", type=float, default=1.0)
    p_disc.set_defaults(func=cmd_vag_discover)

    p_log = sub.add_parser("vag-log", help="Capture raw CAN bus traffic")
    p_log.add_argument("--seconds", type=float, default=10.0)
    p_log.add_argument("--out", help="Append captured frames to this file")
    p_log.set_defaults(func=cmd_vag_log)

    p_vclear = sub.add_parser("vag-clear", help="Experimental: clear fault memory on a specific VAG module")
    p_vclear.add_argument("module", help="Two-hex-digit module address, e.g. 15 for airbag")
    p_vclear.add_argument("--timeout", type=float, default=1.0)
    p_vclear.add_argument("--tx-id", help="Confirmed TP2.0 tx CAN id (hex), from vag-discover")
    p_vclear.add_argument("--rx-id", help="Confirmed TP2.0 rx CAN id (hex), from vag-discover")
    p_vclear.set_defaults(func=cmd_vag_clear)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\nInterrupted.")
        return 130
    except Exception as exc:  # top-level, user-facing: report cleanly instead of a raw traceback
        console.print(f"[red]Error: {exc}[/red]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
