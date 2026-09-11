"""Small reusable widgets: a bar-style gauge for live PID values and a
colored status dot, both plain ttk/Canvas - no extra GUI dependency."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class Gauge(ttk.Frame):
    def __init__(self, parent, title: str, unit: str, min_value: float, max_value: float, warn_from: float | None = None):
        super().__init__(parent, style="Card.TFrame", padding=(14, 10))
        self.min_value = min_value
        self.max_value = max_value
        self.warn_from = warn_from

        ttk.Label(self, text=title.upper(), style="CardTitle.TLabel").pack(anchor="w")
        value_row = ttk.Frame(self, style="Card.TFrame")
        value_row.pack(anchor="w", fill="x")
        self._value_label = ttk.Label(value_row, text="--", style="CardValue.TLabel")
        self._value_label.pack(side="left")
        ttk.Label(value_row, text=f" {unit}", style="Card.TLabel").pack(side="left", padx=(2, 0))

        self._canvas = tk.Canvas(self, height=8, bg="#e7ebef", highlightthickness=0)
        self._canvas.pack(fill="x", pady=(8, 0))
        self._fill = self._canvas.create_rectangle(0, 0, 0, 8, fill=theme.ACCENT, width=0)
        self.bind("<Configure>", lambda _e: self._redraw())
        self._last_fraction = 0.0

    def set_value(self, value: float | None) -> None:
        if value is None:
            self._value_label.configure(text="n/d")
            self._last_fraction = 0.0
        else:
            self._value_label.configure(text=f"{value:g}")
            span = self.max_value - self.min_value or 1
            self._last_fraction = max(0.0, min(1.0, (value - self.min_value) / span))
            color = theme.ACCENT
            if self.warn_from is not None and value >= self.warn_from:
                color = theme.DANGER
            self._canvas.itemconfigure(self._fill, fill=color)
        self._redraw()

    def _redraw(self) -> None:
        width = self._canvas.winfo_width() or 1
        self._canvas.coords(self._fill, 0, 0, width * self._last_fraction, 8)


class StatusDot(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style="Header.TFrame")
        self._canvas = tk.Canvas(self, width=12, height=12, bg=theme.HEADER_BG, highlightthickness=0)
        self._canvas.pack()
        self._dot = self._canvas.create_oval(2, 2, 10, 10, fill="#5b6b79", width=0)

    def set_state(self, connected: bool) -> None:
        self._canvas.itemconfigure(self._dot, fill=theme.OK_GREEN if connected else "#5b6b79")
