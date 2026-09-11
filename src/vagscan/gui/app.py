"""VAGScan desktop GUI: a workshop-scan-tool-style front end (sidebar nav,
connection bar, card dashboard) over the same session/service classes the
CLI uses - no protocol logic lives here, only presentation and the
IOWorker plumbing that keeps serial I/O off the Tk main thread.
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from vagscan.app.full_scan import FullScanner
from vagscan.app.session import VagscanSession
from vagscan.transport import autodetect_elm327, list_serial_ports
from vagscan.vag.addresses import MODULE_ADDRESSES
from vagscan.vag.kwp2000 import KWP2000Client, KWP2000Error
from vagscan.vag.tp20 import TP20Timeout

from . import theme
from .io_worker import IOWorker
from .widgets import Gauge, StatusDot

logger = logging.getLogger(__name__)

LIVE_PIDS = [
    ("0C", "RPM", "rpm", 0, 7000, 6000),
    ("0D", "Velocidad", "km/h", 0, 220, None),
    ("05", "Temp. refrigerante", "°C", -40, 130, 110),
    ("11", "Acelerador", "%", 0, 100, None),
    ("0F", "Temp. admisión", "°C", -40, 80, None),
    ("42", "Voltaje batería", "V", 8, 16, None),
]

LIVE_INTERVAL_MS = 600

ABOUT_TEXT = """VAGScan - lo que hace y sus límites

Cómo se usa: enchufá el ELM327 al conector OBD del auto, emparejalo por
Bluetooth en la PC, abrí la app y tocá "Buscar adaptador" (se hace solo al
arrancar). Cuando diga Conectado, tocá ESCANEAR: lee el motor por OBD-II y
después pregunta módulo por módulo cuáles responden.

Capa OBD-II genérica (Escaneo, Fallas, En vivo): estándar, funciona en
cualquier auto compatible OBD-II. Leer y borrar fallas del motor por acá
sí actúa sobre la ECU real (Mode 03/04 de OBD-II).

Qué manda esta app al bus del auto:
- Escaneo normal (con la casilla VAG sin tildar): SOLO pedidos de lectura
  OBD-II estándar (03, 07, 0A, 0902, 01xx). Lo mismo que manda cualquier
  escáner comercial. Nada más.
- Borrar fallas: el comando estándar 04. Es la única escritura, es
  reversible (si la causa sigue, el código vuelve) y pide frase de
  confirmación.
- Sonda de módulos VAG (casilla opcional): una trama de 8 bytes por
  módulo, ~17 en total. Antes de transmitir, la app escucha el bus y si
  el ID que iba a usar ya lo está usando un módulo real del auto, NO
  transmite nada y cancela la sonda.

No hay flasheo, ni codificación, ni escritura de adaptaciones, ni nada de
inmovilizador. El "security access" (lo único que podría dejar un módulo
bloqueado por reintentos fallidos) no se invoca desde ningún lado.

Capa VAG avanzada (pestaña "VAG avanzado"): EXPERIMENTAL. El Simos 7.1
de este auto habla el protocolo propietario de VAG (KWP2000 sobre CAN,
TP2.0), que el chip ELM327 no soporta de fábrica. Los IDs de canal que
usás ahí salen de "Descubrir" contra el auto real - no son automáticos
ni están garantizados hasta que los validés vos mismo.

Seguridad al borrar fallas:
- Borrar fallas genéricas reinicia el testigo (MIL) y los monitores de
  emisiones - el auto va a reportar "no listo" hasta completar un ciclo
  de manejo completo.
- Borrar fallas del módulo de airbag/SRS (dirección 15) NO arregla lo
  que la originó. Si el problema sigue presente (por ejemplo un circuito
  de un pretensor de cinturón), el sistema puede no funcionar
  correctamente en un choque real. Nunca midás un circuito pirotécnico
  con un testímetro común.
- No hay ningún botón "aceptar todo": cada borrado pide escribir una
  frase de confirmación exacta.

Fuera de alcance a propósito: bypass de inmovilizador, algoritmos de
seed-key de "security access". Esta herramienta no lo implementa ni lo
adivina.

El criterio final sobre qué hacer con cualquier lectura es tuyo.
"""


class VagscanApp(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root = root
        self.session: VagscanSession | None = None
        self.streaming = False
        self._dtc_rows: dict[str, tuple[str, object]] = {}
        self._scan_rows: dict[str, object] = {}
        self._last_scan = None
        self._tp20_channel = None
        # The scan runs on the IO thread but its progress has to be drawn on
        # the UI thread, so it goes through a queue the poll loop drains -
        # same reason IOWorker doesn't call back into Tk directly.
        self._progress_queue: queue.Queue = queue.Queue()

        theme.apply(root)
        self.worker = IOWorker()
        self._poll_worker()

        self.pack(fill="both", expand=True)
        self._build_header()
        self._build_body()
        self._build_statusbar()
        self._show_page("panel")
        self._refresh_ports()
        self._set_connected_state(False)

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Look for the adapter straight away, so the common case is "plug it
        # in, open the app, it's already found it".
        self.root.after(200, self._autodetect_adapter)

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Header.TFrame", padding=(16, 10))
        header.pack(fill="x")

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="VAGScan", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="Seat León Mk2 1.6 BSE · Simos 7.1", style="HeaderSub.TLabel").pack(anchor="w")

        conn_box = ttk.Frame(header, style="Header.TFrame")
        conn_box.pack(side="right")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(conn_box, textvariable=self.port_var, width=22, state="readonly")
        self.port_combo.pack(side="left", padx=(0, 6))
        self.detect_btn = ttk.Button(conn_box, text="Buscar adaptador", command=self._autodetect_adapter)
        self.detect_btn.pack(side="left", padx=(0, 6))
        self.connect_btn = ttk.Button(conn_box, text="Conectar", style="Accent.TButton", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=(0, 12))
        self.status_dot = StatusDot(conn_box)
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_label = ttk.Label(conn_box, text="Desconectado", style="StatusBad.TLabel")
        self.status_label.pack(side="left")

    def _build_body(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        sidebar = ttk.Frame(body, style="Sidebar.TFrame", width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in [
            ("panel", "Escaneo"),
            ("dtc", "Fallas (DTC)"),
            ("live", "En vivo"),
            ("vag", "VAG avanzado"),
            ("about", "Seguridad / Acerca de"),
        ]:
            btn = ttk.Button(sidebar, text=label, style="Sidebar.TButton", command=lambda k=key: self._show_page(k))
            btn.pack(fill="x")
            self.nav_buttons[key] = btn

        content = ttk.Frame(body, padding=16)
        content.pack(side="left", fill="both", expand=True)

        self.pages: dict[str, ttk.Frame] = {
            "panel": self._build_panel_page(content),
            "dtc": self._build_dtc_page(content),
            "live": self._build_live_page(content),
            "vag": self._build_vag_page(content),
            "about": self._build_about_page(content),
        }
        for page in self.pages.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, style="Header.TFrame", padding=(12, 4))
        bar.pack(fill="x", side="bottom")
        self.status_msg = ttk.Label(bar, text="Listo.", style="HeaderSub.TLabel")
        self.status_msg.pack(side="left")

    def _show_page(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            btn.configure(style="SidebarActive.TButton" if k == key else "Sidebar.TButton")
        self.pages[key].tkraise()

    # ------------------------------------------------------------------
    # scan page - the main screen
    # ------------------------------------------------------------------
    def _build_panel_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = ttk.Frame(parent)

        title_row = ttk.Frame(page)
        title_row.pack(fill="x", pady=(0, 4))
        ttk.Label(title_row, text="Escaneo del vehículo", font=(theme.FONT_FAMILY, 14, "bold")).pack(side="left")
        self.scan_btn = ttk.Button(title_row, text="ESCANEAR", style="Accent.TButton", command=self._start_scan)
        self.scan_btn.pack(side="right")

        # Off by default: with this unchecked a scan is nothing but standard
        # OBD-II requests, which is what any commercial scan tool sends.
        self.scan_vag_var = tk.BooleanVar(value=False)
        option_row = ttk.Frame(page)
        option_row.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(
            option_row,
            text="Además, sondear módulos VAG (experimental: emite tramas propias al bus)",
            variable=self.scan_vag_var,
        ).pack(side="left")

        cards = ttk.Frame(page)
        cards.pack(fill="x")
        self._panel_labels: dict[str, ttk.Label] = {}
        for key, title in [("ati", "Adaptador"), ("protocol", "Protocolo"), ("voltage", "Voltaje"), ("vin", "VIN")]:
            card = ttk.Frame(cards, style="Card.TFrame", padding=(14, 10))
            card.pack(side="left", padx=(0, 10), fill="both", expand=True)
            ttk.Label(card, text=title.upper(), style="CardTitle.TLabel").pack(anchor="w")
            value = ttk.Label(card, text="--", style="Card.TLabel", font=(theme.FONT_FAMILY, 12, "bold"))
            value.pack(anchor="w", pady=(4, 0))
            self._panel_labels[key] = value

        progress_row = ttk.Frame(page)
        progress_row.pack(fill="x", pady=(14, 4))
        self.scan_progress = ttk.Progressbar(progress_row, mode="determinate", maximum=100)
        self.scan_progress.pack(fill="x")
        self.scan_progress_label = ttk.Label(page, text="Conectá el adaptador y tocá ESCANEAR.", style="Muted.TLabel")
        self.scan_progress_label.pack(anchor="w")

        self.scan_tree = ttk.Treeview(page, columns=("detalle", "estado"), show="tree headings", height=11)
        self.scan_tree.heading("#0", text="Elemento")
        self.scan_tree.heading("detalle", text="Detalle")
        self.scan_tree.heading("estado", text="Estado")
        self.scan_tree.column("#0", width=230, anchor="w")
        self.scan_tree.column("detalle", width=380, anchor="w")
        self.scan_tree.column("estado", width=175, anchor="w")
        self.scan_tree.pack(fill="both", expand=True, pady=(10, 8))
        self.scan_tree.bind("<<TreeviewSelect>>", self._on_scan_select)

        self.scan_detail = tk.Text(page, height=5, wrap="word", relief="flat", bg=theme.CARD_BG, state="disabled")
        self.scan_detail.pack(fill="x")

        action_row = ttk.Frame(page)
        action_row.pack(fill="x", pady=(10, 0))
        self.scan_clear_btn = ttk.Button(
            action_row,
            text="Borrar las fallas encontradas",
            style="Danger.TButton",
            command=self._clear_scanned_dtcs,
            state="disabled",
        )
        self.scan_clear_btn.pack(side="left")
        self.scan_summary_label = ttk.Label(action_row, text="", style="Muted.TLabel")
        self.scan_summary_label.pack(side="left", padx=(12, 0))
        return page

    def _start_scan(self) -> None:
        if self.session is None:
            messagebox.showwarning("Sin conexión", "Primero conectá el adaptador.")
            return
        self.streaming = False  # a live-data loop would compete for the adapter
        self.live_toggle_btn.configure(text="Iniciar")
        self.scan_tree.delete(*self.scan_tree.get_children())
        self._scan_rows.clear()
        self.scan_detail.configure(state="normal")
        self.scan_detail.delete("1.0", "end")
        self.scan_detail.configure(state="disabled")
        self.scan_clear_btn.configure(state="disabled")
        self.scan_summary_label.configure(text="")
        self.scan_progress.configure(value=0)
        self._last_scan = None

        scanner = FullScanner(self.session, scan_vag_modules=self.scan_vag_var.get())

        def job():
            # Note the lambda: Queue.put's own signature is (item, block,
            # timeout), so passing it as the 3-arg progress callback directly
            # would quietly drop the counts instead of queueing a tuple.
            return scanner.run(progress=lambda label, current, total: self._progress_queue.put((label, current, total)))

        def on_success(result):
            self._last_scan = result
            self._render_scan_result(result)

        def on_error(exc):
            self.scan_progress_label.configure(text=f"El escaneo falló: {exc}")
            self._set_status(f"Escaneo fallido: {exc}", "error")
            messagebox.showerror("Escaneo fallido", str(exc))

        self._set_status("Escaneando...", "info")
        self._run(job, on_success, on_error, busy_widgets=(self.scan_btn,))

    def _render_scan_result(self, result) -> None:
        self._panel_labels["ati"].configure(text=result.adapter or "?")
        self._panel_labels["protocol"].configure(text=result.protocol or "desconocido")
        self._panel_labels["voltage"].configure(text=result.voltage or "?")
        self._panel_labels["vin"].configure(text=result.vin or "no disponible")

        engine_node = self.scan_tree.insert(
            "", "end", text="Motor (OBD-II genérico)", values=("", f"{len(result.dtcs)} falla(s)"), open=True
        )
        if not result.dtcs:
            self.scan_tree.insert(engine_node, "end", text="Sin fallas", values=("No hay códigos almacenados.", "OK"))
        for found in result.dtcs:
            iid = self.scan_tree.insert(
                engine_node, "end", text=found.dtc.code, values=(found.title, f"{found.kind} · {found.severity}")
            )
            self._scan_rows[iid] = found

        modules_node = self.scan_tree.insert(
            "",
            "end",
            text="Módulos VAG (experimental)",
            values=("", f"{len(result.responding_modules)} respondieron"),
            open=True,
        )
        for module in result.modules:
            if module.responded:
                detail = ", ".join(f"ID {f.can_id_hex}" for f in module.frames[:3])
                state = "respondió"
            else:
                detail = module.note or "sin respuesta"
                state = "silencio"
            self.scan_tree.insert(modules_node, "end", text=f"{module.address} - {module.name}", values=(detail, state))

        if result.warnings:
            warn_node = self.scan_tree.insert("", "end", text="Avisos", values=("", f"{len(result.warnings)}"), open=False)
            for warning in result.warnings:
                self.scan_tree.insert(warn_node, "end", text="", values=(warning, ""))

        self.scan_progress.configure(value=100)
        self.scan_progress_label.configure(text="Escaneo completo.")
        self.scan_summary_label.configure(text=result.summary_line())
        self.scan_clear_btn.configure(state="normal" if result.dtcs else "disabled")
        self._set_status(result.summary_line(), "ok" if not result.dtcs else "warn")

    def _on_scan_select(self, _event) -> None:
        selection = self.scan_tree.selection()
        self.scan_detail.configure(state="normal")
        self.scan_detail.delete("1.0", "end")
        if selection:
            found = self._scan_rows.get(selection[0])
            if found is not None and found.info is not None:
                text = f"{found.dtc.code} - {found.info.title}\n\n{found.info.description}"
                if found.info.common_causes:
                    text += "\n\nCausas comunes: " + "; ".join(found.info.common_causes)
                if found.info.suggested_checks:
                    text += "\n\nRevisar: " + "; ".join(found.info.suggested_checks)
                self.scan_detail.insert("1.0", text)
            elif found is not None:
                self.scan_detail.insert("1.0", f"{found.dtc.code}: no está en la base local todavía.")
        self.scan_detail.configure(state="disabled")

    def _clear_scanned_dtcs(self) -> None:
        if self.session is None or not self._last_scan or not self._last_scan.dtcs:
            return
        codes = ", ".join(sorted({f.dtc.code for f in self._last_scan.dtcs}))

        def do_clear():
            def job():
                self.session.obd2.clear_dtcs()

            def on_success(_result):
                self._set_status("Fallas borradas en la ECU. Re-escaneando para verificar...", "ok")
                self._start_scan()  # re-read so you see what actually cleared and what came back

            self._run(job, on_success, busy_widgets=(self.scan_clear_btn,))

        self._confirm_dialog(
            "Borrar fallas en la ECU",
            f"Se van a borrar de la ECU real: {codes}.\n\n"
            "Esto reinicia el testigo (MIL) y los monitores de emisiones - el auto va a reportar "
            "\"no listo\" hasta completar un ciclo de manejo. Si la causa sigue presente, el código vuelve.",
            "BORRAR",
            do_clear,
        )

    # ------------------------------------------------------------------
    # DTC page
    # ------------------------------------------------------------------
    def _build_dtc_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = ttk.Frame(parent)
        ttk.Label(page, text="Fallas (DTC)", font=(theme.FONT_FAMILY, 14, "bold")).pack(anchor="w", pady=(0, 12))

        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x", pady=(0, 8))
        self.dtc_read_btn = ttk.Button(toolbar, text="Leer fallas", style="Accent.TButton", command=self._dtc_read)
        self.dtc_read_btn.pack(side="left")
        self.dtc_clear_btn = ttk.Button(toolbar, text="Borrar fallas (genérico)", style="Danger.TButton", command=self._dtc_clear)
        self.dtc_clear_btn.pack(side="left", padx=(8, 0))

        columns = ("tipo", "codigo", "titulo", "severidad")
        self.dtc_tree = ttk.Treeview(page, columns=columns, show="headings", height=10)
        for col, label, width in [
            ("tipo", "Tipo", 90),
            ("codigo", "Código", 80),
            ("titulo", "Título", 320),
            ("severidad", "Severidad", 100),
        ]:
            self.dtc_tree.heading(col, text=label)
            self.dtc_tree.column(col, width=width, anchor="w")
        self.dtc_tree.pack(fill="both", expand=True)
        self.dtc_tree.bind("<<TreeviewSelect>>", self._on_dtc_select)

        ttk.Label(page, text="Detalle", style="CardTitle.TLabel").pack(anchor="w", pady=(10, 2))
        self.dtc_detail = tk.Text(page, height=6, wrap="word", relief="flat", bg=theme.CARD_BG, state="disabled")
        self.dtc_detail.pack(fill="x")
        return page

    def _dtc_read(self) -> None:
        if self.session is None:
            return

        def job():
            session = self.session
            return {
                "Almacenada": session.obd2.read_stored_dtcs(),
                "Pendiente": session.obd2.read_pending_dtcs(),
                "Permanente": session.obd2.read_permanent_dtcs(),
            }

        def on_success(by_kind):
            self.dtc_tree.delete(*self.dtc_tree.get_children())
            self._dtc_rows.clear()
            count = 0
            for kind, dtcs in by_kind.items():
                for dtc in dtcs:
                    matches = self.session.dtc_db.lookup(dtc.code)
                    title = matches[0].title if matches else "(no está en la base local)"
                    severity = matches[0].severity if matches else "desconocida"
                    iid = self.dtc_tree.insert("", "end", values=(kind, dtc.code, title, severity))
                    self._dtc_rows[iid] = (dtc.code, matches[0] if matches else None)
                    count += 1
            self._set_status(f"{count} fallas encontradas." if count else "Sin fallas almacenadas.", "ok")

        self._run(job, on_success, busy_widgets=(self.dtc_read_btn,))

    def _on_dtc_select(self, _event) -> None:
        selection = self.dtc_tree.selection()
        self.dtc_detail.configure(state="normal")
        self.dtc_detail.delete("1.0", "end")
        if selection:
            _code, info = self._dtc_rows.get(selection[0], (None, None))
            if info is not None:
                text = info.description
                if info.common_causes:
                    text += "\n\nCausas comunes: " + "; ".join(info.common_causes)
                if info.suggested_checks:
                    text += "\n\nRevisar: " + "; ".join(info.suggested_checks)
                self.dtc_detail.insert("1.0", text)
            else:
                self.dtc_detail.insert("1.0", "Este código no está en la base local todavía.")
        self.dtc_detail.configure(state="disabled")

    def _dtc_clear(self) -> None:
        if self.session is None:
            return

        def do_clear():
            def job():
                self.session.obd2.clear_dtcs()

            def on_success(_result):
                self._set_status("Fallas genéricas borradas.", "ok")
                messagebox.showinfo("Listo", "Se borraron las fallas genéricas (Mode 04).")
                self._dtc_read()

            self._run(job, on_success, busy_widgets=(self.dtc_clear_btn,))

        self._confirm_dialog(
            "Borrar fallas",
            "Esto reinicia el testigo (MIL) y los monitores de emisiones. "
            "El auto va a reportar \"no listo\" hasta completar un ciclo de manejo.",
            "BORRAR",
            do_clear,
        )

    # ------------------------------------------------------------------
    # live page
    # ------------------------------------------------------------------
    def _build_live_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = ttk.Frame(parent)
        header_row = ttk.Frame(page)
        header_row.pack(fill="x", pady=(0, 12))
        ttk.Label(header_row, text="En vivo", font=(theme.FONT_FAMILY, 14, "bold")).pack(side="left")
        self.live_toggle_btn = ttk.Button(header_row, text="Iniciar", style="Accent.TButton", command=self._toggle_live)
        self.live_toggle_btn.pack(side="right")

        grid = ttk.Frame(page)
        grid.pack(fill="both", expand=True)
        self.gauges: dict[str, Gauge] = {}
        for i, (pid, label, unit, lo, hi, warn) in enumerate(LIVE_PIDS):
            gauge = Gauge(grid, label, unit, lo, hi, warn)
            gauge.grid(row=i // 3, column=i % 3, sticky="nsew", padx=8, pady=8)
            grid.columnconfigure(i % 3, weight=1)
            self.gauges[pid] = gauge
        return page

    def _toggle_live(self) -> None:
        if self.session is None:
            return
        self.streaming = not self.streaming
        self.live_toggle_btn.configure(text="Detener" if self.streaming else "Iniciar")
        if self.streaming:
            self._live_poll()

    def _live_poll(self) -> None:
        if not self.streaming or self.session is None:
            return

        def job():
            return self.session.obd2.read_pids([pid for pid, *_ in LIVE_PIDS])

        def on_success(values):
            for pid, value in values.items():
                if pid in self.gauges:
                    self.gauges[pid].set_value(value)
            if self.streaming:
                self.root.after(LIVE_INTERVAL_MS, self._live_poll)

        def on_error(exc):
            self._set_status(f"En vivo: {exc}", "error")
            if self.streaming:
                self.root.after(LIVE_INTERVAL_MS, self._live_poll)

        self._run(job, on_success, on_error)

    # ------------------------------------------------------------------
    # VAG (experimental) page
    # ------------------------------------------------------------------
    def _build_vag_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = ttk.Frame(parent)
        ttk.Label(page, text="VAG avanzado (experimental)", font=(theme.FONT_FAMILY, 14, "bold")).pack(anchor="w")
        ttk.Label(
            page,
            text="Los IDs de canal no son automáticos: descubrí candidatos y confirmálos antes de borrar nada.",
            style="Muted.TLabel",
            wraplength=560,
        ).pack(anchor="w", pady=(2, 12))

        module_row = ttk.Frame(page)
        module_row.pack(fill="x", pady=(0, 8))
        ttk.Label(module_row, text="Módulo:").pack(side="left")
        self._module_labels = [f"{m.address} - {m.name}" for m in MODULE_ADDRESSES.values()]
        self.module_var = tk.StringVar(value=self._module_labels[0])
        ttk.Combobox(module_row, textvariable=self.module_var, values=self._module_labels, state="readonly", width=32).pack(
            side="left", padx=(6, 12)
        )
        self.vag_discover_btn = ttk.Button(module_row, text="Descubrir canal", command=self._vag_discover)
        self.vag_discover_btn.pack(side="left")

        self.vag_frames_list = tk.Listbox(page, height=5)
        self.vag_frames_list.pack(fill="x", pady=(0, 8))

        id_row = ttk.Frame(page)
        id_row.pack(fill="x", pady=(0, 8))
        ttk.Label(id_row, text="TX id (hex):").pack(side="left")
        self.tx_id_var = tk.StringVar()
        ttk.Entry(id_row, textvariable=self.tx_id_var, width=8).pack(side="left", padx=(4, 12))
        ttk.Label(id_row, text="RX id (hex):").pack(side="left")
        self.rx_id_var = tk.StringVar()
        ttk.Entry(id_row, textvariable=self.rx_id_var, width=8).pack(side="left", padx=(4, 12))
        self.vag_clear_btn = ttk.Button(id_row, text="Borrar fallos del módulo", style="Danger.TButton", command=self._vag_clear)
        self.vag_clear_btn.pack(side="left")

        ttk.Separator(page).pack(fill="x", pady=12)

        capture_row = ttk.Frame(page)
        capture_row.pack(fill="x")
        ttk.Label(capture_row, text="Captura de bus (segundos):").pack(side="left")
        self.capture_seconds_var = tk.StringVar(value="10")
        ttk.Entry(capture_row, textvariable=self.capture_seconds_var, width=6).pack(side="left", padx=(4, 12))
        self.capture_btn = ttk.Button(capture_row, text="Capturar", command=self._bus_capture)
        self.capture_btn.pack(side="left")
        self.save_capture_btn = ttk.Button(capture_row, text="Guardar...", command=self._save_capture, state="disabled")
        self.save_capture_btn.pack(side="left", padx=(8, 0))
        self.capture_status = ttk.Label(page, text="", style="Muted.TLabel")
        self.capture_status.pack(anchor="w", pady=(6, 0))
        return page

    def _vag_discover(self) -> None:
        if self.session is None:
            return
        address = self.module_var.get().split(" - ")[0]

        def job():
            return self.session.tp20.discover_channel(address, timeout=1.0)

        def on_success(frames):
            self.vag_frames_list.delete(0, "end")
            if not frames:
                self.vag_frames_list.insert("end", "Sin respuesta - módulo ausente o formato de sonda incorrecto.")
            for f in frames:
                self.vag_frames_list.insert("end", f"ID {f.can_id_hex}   datos {f.data.hex().upper()}")
            self._set_status(f"{len(frames)} tramas candidatas para el módulo {address}.", "ok")

        self._run(job, on_success, busy_widgets=(self.vag_discover_btn,))

    def _vag_clear(self) -> None:
        if self.session is None:
            return
        address = self.module_var.get().split(" - ")[0]
        tx_text, rx_text = self.tx_id_var.get().strip(), self.rx_id_var.get().strip()
        if not tx_text or not rx_text:
            messagebox.showwarning("Faltan datos", "Completá TX id y RX id (obtenélos con \"Descubrir canal\").")
            return
        try:
            tx_id, rx_id = int(tx_text, 16), int(rx_text, 16)
        except ValueError:
            messagebox.showerror("Valor inválido", "TX id y RX id deben ser hexadecimales, ej: 300")
            return

        module_name = self.module_var.get().split(" - ", 1)[1] if " - " in self.module_var.get() else address
        is_airbag = address.upper() == "15"

        def do_clear():
            def job():
                channel = self.session.tp20.open_channel(address, tx_id, rx_id)
                kwp = KWP2000Client(self.session.tp20, channel)
                kwp.clear_diagnostic_information()

            def on_success(_result):
                self._set_status(f"Comando de borrado enviado al módulo {address}.", "ok")
                messagebox.showinfo("Listo", f"Se envió el comando de borrado al módulo {address} ({module_name}).")

            def on_error(exc):
                if isinstance(exc, (KWP2000Error, TP20Timeout)):
                    messagebox.showerror("Rechazado por la ECU", str(exc))
                else:
                    messagebox.showerror("Error", str(exc))
                self._set_status(f"Error borrando módulo {address}: {exc}", "error")

            self._run(job, on_success, on_error, busy_widgets=(self.vag_clear_btn,))

        if is_airbag:
            warning = (
                "Módulo de AIRBAG/SRS. Borrar esto NO arregla lo que originó la falla: si el problema "
                "sigue físicamente presente (ej. un circuito de pretensor), el sistema puede no funcionar "
                "correctamente en un choque real. Solo continuá si ya reparáste la causa real."
            )
            phrase = "ENTIENDO EL RIESGO"
        else:
            warning = f"Se va a borrar la memoria de fallas del módulo {address} ({module_name})."
            phrase = "BORRAR"
        self._confirm_dialog("Borrar fallos del módulo", warning, phrase, do_clear)

    def _bus_capture(self) -> None:
        if self.session is None:
            return
        try:
            seconds = float(self.capture_seconds_var.get())
        except ValueError:
            messagebox.showerror("Valor inválido", "Los segundos deben ser un número.")
            return

        def job():
            return self.session.bus_logger.capture_for(seconds)

        def on_success(frames):
            self.capture_status.configure(text=f"{len(frames)} tramas capturadas.")
            self.save_capture_btn.configure(state="normal" if frames else "disabled")
            self._set_status(f"Captura de bus: {len(frames)} tramas.", "ok")

        self._run(job, on_success, busy_widgets=(self.capture_btn,))

    def _save_capture(self) -> None:
        if self.session is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".log", filetypes=[("Log", "*.log"), ("Todos", "*.*")])
        if not path:
            return
        self.session.bus_logger.save(path)
        self._set_status(f"Captura guardada en {path}", "ok")

    # ------------------------------------------------------------------
    # about page
    # ------------------------------------------------------------------
    def _build_about_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = ttk.Frame(parent)
        ttk.Label(page, text="Seguridad / Acerca de", font=(theme.FONT_FAMILY, 14, "bold")).pack(anchor="w", pady=(0, 12))
        text = tk.Text(page, wrap="word", relief="flat", bg=theme.CARD_BG, padx=12, pady=12)
        text.insert("1.0", ABOUT_TEXT)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        return page

    # ------------------------------------------------------------------
    # connection lifecycle
    # ------------------------------------------------------------------
    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        values = [p.device for p in ports]
        self.port_combo.configure(values=values)
        preferred = next((p.device for p in ports if p.likely_elm327), None)
        if preferred:
            self.port_var.set(preferred)
        elif values and not self.port_var.get():
            self.port_var.set(values[0])

    def _autodetect_adapter(self, *, then_connect: bool = True) -> None:
        """Talk to each serial port in turn and see which one answers like an
        ELM327, instead of making the user guess which COM port it is."""
        if self.session is not None:
            return
        self._refresh_ports()
        self._set_status("Buscando el adaptador ELM327...", "info")
        self.scan_progress_label.configure(text="Buscando el adaptador ELM327...")

        def job():
            return autodetect_elm327()

        def on_success(found):
            if found is None:
                self._set_status(
                    "No se encontró ningún ELM327. ¿Está enchufado al auto y emparejado por Bluetooth?", "warn"
                )
                self.scan_progress_label.configure(
                    text="No se encontró el adaptador. Enchufálo al conector OBD, emparejá el Bluetooth y tocá "
                    "\"Buscar adaptador\"."
                )
                return
            self.port_var.set(found.device)
            self._set_status(f"Adaptador encontrado en {found.device}: {found.identity}", "ok")
            if then_connect:
                self._connect()

        def on_error(exc):
            self._set_status(f"Error buscando el adaptador: {exc}", "error")

        self._run(job, on_success, on_error, busy_widgets=(self.detect_btn, self.connect_btn))

    def _toggle_connect(self) -> None:
        if self.session is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("Sin puerto", "Elegí un puerto serie (botón ↻ para actualizar la lista).")
            return
        self.connect_btn.configure(state="disabled")
        self._set_status(f"Conectando a {port}...", "info")

        def job():
            return VagscanSession.connect(port)

        def on_success(session):
            self.session = session
            self._set_connected_state(True)
            self._set_status(f"Conectado a {port}. Tocá ESCANEAR para leer todo el vehículo.", "ok")
            self.scan_progress_label.configure(text="Conectado. Tocá ESCANEAR para leer todo el vehículo.")

        def on_error(exc):
            self.connect_btn.configure(state="normal")
            self._set_status(f"No se pudo conectar: {exc}", "error")
            messagebox.showerror("Error de conexión", str(exc))

        self._run(job, on_success, on_error)

    def _disconnect(self) -> None:
        self.streaming = False
        session, self.session = self.session, None
        self._set_connected_state(False)
        if session is not None:
            self.worker.submit(session.close, lambda ok, result: None)
        self._set_status("Desconectado.", "info")

    def _set_connected_state(self, connected: bool) -> None:
        self.status_dot.set_state(connected)
        self.status_label.configure(text="Conectado" if connected else "Desconectado", style="StatusOk.TLabel" if connected else "StatusBad.TLabel")
        self.connect_btn.configure(text="Desconectar" if connected else "Conectar", state="normal")
        for widget in (
            self.scan_btn,
            self.dtc_read_btn,
            self.dtc_clear_btn,
            self.live_toggle_btn,
            self.vag_discover_btn,
            self.vag_clear_btn,
            self.capture_btn,
        ):
            widget.configure(state="normal" if connected else "disabled")
        if not connected:
            self.live_toggle_btn.configure(text="Iniciar")

    def _on_close(self) -> None:
        self.streaming = False
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
        self.root.destroy()

    # ------------------------------------------------------------------
    # shared helpers
    # ------------------------------------------------------------------
    def _poll_worker(self) -> None:
        # The reschedule has to survive anything raised in here: if this
        # callback ever dies, the after() chain stops and every background
        # result silently stops being delivered - the app looks frozen with
        # no error anywhere.
        try:
            self.worker.poll()
            self._drain_progress()
        except Exception as exc:  # noqa: BLE001 - keeping the loop alive matters more
            logger.exception("Error draining background results")
            self._set_status(f"Error interno: {exc}", "error")
        finally:
            self.root.after(30, self._poll_worker)

    def _drain_progress(self) -> None:
        latest = None
        while True:
            try:
                latest = self._progress_queue.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        label, current, total = latest
        self.scan_progress.configure(value=(current / total) * 100 if total else 0)
        self.scan_progress_label.configure(text=f"{label}... ({current}/{total})")

    def _run(self, job, on_success=None, on_error=None, busy_widgets=()):
        for w in busy_widgets:
            w.configure(state="disabled")

        def done(ok: bool, result: object) -> None:
            for w in busy_widgets:
                w.configure(state="normal")
            if ok:
                if on_success is not None:
                    on_success(result)
            else:
                if on_error is not None:
                    on_error(result)
                else:
                    self._set_status(f"Error: {result}", "error")
                    messagebox.showerror("Error", str(result))

        self.worker.submit(job, done)

    def _set_status(self, message: str, level: str = "info") -> None:
        color = {"ok": theme.OK_GREEN, "error": "#e0836f", "warn": theme.WARN_AMBER, "info": "#9db3c6"}.get(level, "#9db3c6")
        self.status_msg.configure(text=message, foreground=color)

    def _confirm_dialog(self, title: str, warning: str, required_phrase: str, on_confirm) -> None:
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=theme.CARD_BG)
        top.transient(self.root)
        top.grab_set()
        top.resizable(False, False)

        ttk.Label(top, text=warning, wraplength=380, background=theme.CARD_BG, foreground=theme.TEXT).pack(
            padx=16, pady=(16, 8)
        )
        ttk.Label(top, text=f'Escribí "{required_phrase}" para confirmar:', background=theme.CARD_BG).pack(
            padx=16, anchor="w"
        )
        entry_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=entry_var, width=32)
        entry.pack(padx=16, pady=(4, 12))
        entry.focus_set()

        def confirm() -> None:
            if entry_var.get().strip() == required_phrase:
                top.destroy()
                on_confirm()
            else:
                top.destroy()
                messagebox.showerror("Confirmación incorrecta", "El texto no coincidía. Se canceló la operación.")

        btn_row = ttk.Frame(top, style="Card.TFrame")
        btn_row.pack(pady=(0, 16))
        ttk.Button(btn_row, text="Cancelar", command=top.destroy).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Confirmar", style="Danger.TButton", command=confirm).pack(side="left", padx=6)
        entry.bind("<Return>", lambda _e: confirm())

        top.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def main() -> None:
    root = tk.Tk()
    root.title("VAGScan")
    root.geometry("1000x660")
    root.minsize(880, 560)
    VagscanApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
