"""Entry point for the packaged executable.

Separate from vagscan.gui.app so PyInstaller has a plain script to analyse,
and so a crash before the window opens still tells the user something -
a packaged app that vanishes silently is impossible to diagnose from the
other end of a chat.
"""
import sys
import traceback


def selftest() -> int:
    """`VAGScan --selftest` - checks the packaged app is intact without
    needing a car or an adapter. Mostly proves the fault-code database got
    bundled: without it the app still opens and still scans, it just reports
    every code as unknown, which is a miserable thing to discover in a
    garage."""
    from vagscan.dtc_db import DtcDatabase
    from vagscan.transport import list_serial_ports

    db = DtcDatabase.load_default()
    sample = db.lookup("P0300")
    ports = list_serial_ports()
    ok = len(db) > 20 and bool(sample)
    report = "\n".join(
        [
            f"Base de fallas: {len(db)} entradas cargadas",
            f"Consulta de prueba P0300: {sample[0].title if sample else 'NO ENCONTRADA'}",
            f"Puertos serie visibles: {[p.device for p in ports] or 'ninguno'}",
            "",
            "RESULTADO: " + ("OK" if ok else "FALLA - la base de fallas no se empaquetó bien"),
        ]
    )
    print(report)
    if "--no-dialog" in sys.argv:
        # Automated checks (CI) have no one to click OK, and a modal dialog
        # would hang the build forever.
        return 0 if ok else 1
    # A windowed build has nowhere to print, so show it too.
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("VAGScan - autodiagnóstico", report)
        root.destroy()
    except Exception:
        pass
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    try:
        from vagscan.gui.app import main as gui_main

        gui_main()
        return 0
    except Exception:
        details = traceback.format_exc()
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("VAGScan no pudo iniciar", details)
        except Exception:
            print(details, file=sys.stderr)
            input("VAGScan no pudo iniciar. Copiá este error y enter para salir...")
        return 1


if __name__ == "__main__":
    sys.exit(main())
