"""Colors and ttk styling. Aims for the look of the workshop scan-tool
software this project is explicitly modeled on (INFOCAR and similar): a
dark sidebar for navigation, a light content area with card-style panels,
one accent color for primary actions, and a separate danger color reserved
for anything that clears fault memory.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#eef1f5"
CARD_BG = "#ffffff"
HEADER_BG = "#132638"
HEADER_FG = "#ffffff"
SIDEBAR_BG = "#152a3d"
SIDEBAR_FG = "#c3d1de"
SIDEBAR_FG_ACTIVE = "#ffffff"
SIDEBAR_ACTIVE_BG = "#1f7ae0"
ACCENT = "#1f7ae0"
ACCENT_DARK = "#155ba8"
DANGER = "#c0392b"
DANGER_DARK = "#96281c"
OK_GREEN = "#219653"
WARN_AMBER = "#c07c1e"
TEXT = "#1b2733"
TEXT_MUTED = "#5b6b79"
BORDER = "#d5dce3"

FONT_FAMILY = "Helvetica"
_FONT_BY_PLATFORM = {"win32": "Segoe UI", "aqua": "Helvetica Neue", "x11": "DejaVu Sans"}


def apply(root: tk.Tk) -> None:
    windowing_system = root.tk.call("tk", "windowingsystem")
    global FONT_FAMILY
    FONT_FAMILY = _FONT_BY_PLATFORM.get(windowing_system, "Helvetica")
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, font=(FONT_FAMILY, 10))
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD_BG, relief="flat")
    style.configure("Header.TFrame", background=HEADER_BG)
    style.configure("Sidebar.TFrame", background=SIDEBAR_BG)

    style.configure("Header.TLabel", background=HEADER_BG, foreground=HEADER_FG, font=(FONT_FAMILY, 13, "bold"))
    style.configure("HeaderSub.TLabel", background=HEADER_BG, foreground="#9db3c6", font=(FONT_FAMILY, 9))
    style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT)
    style.configure("CardTitle.TLabel", background=CARD_BG, foreground=TEXT_MUTED, font=(FONT_FAMILY, 9, "bold"))
    style.configure("CardValue.TLabel", background=CARD_BG, foreground=TEXT, font=(FONT_FAMILY, 20, "bold"))
    style.configure("Muted.TLabel", background=BG, foreground=TEXT_MUTED)
    style.configure("StatusOk.TLabel", background=HEADER_BG, foreground=OK_GREEN, font=(FONT_FAMILY, 10, "bold"))
    style.configure("StatusBad.TLabel", background=HEADER_BG, foreground="#e0836f", font=(FONT_FAMILY, 10, "bold"))

    style.configure(
        "Sidebar.TButton",
        background=SIDEBAR_BG,
        foreground=SIDEBAR_FG,
        borderwidth=0,
        focusthickness=0,
        padding=(16, 12),
        font=(FONT_FAMILY, 10),
        anchor="w",
    )
    style.map(
        "Sidebar.TButton",
        background=[("active", SIDEBAR_ACTIVE_BG), ("pressed", SIDEBAR_ACTIVE_BG)],
        foreground=[("active", SIDEBAR_FG_ACTIVE), ("pressed", SIDEBAR_FG_ACTIVE)],
    )
    style.configure(
        "SidebarActive.TButton",
        background=SIDEBAR_ACTIVE_BG,
        foreground=SIDEBAR_FG_ACTIVE,
        borderwidth=0,
        padding=(16, 12),
        font=(FONT_FAMILY, 10, "bold"),
        anchor="w",
    )
    style.map("SidebarActive.TButton", background=[("active", SIDEBAR_ACTIVE_BG)])

    style.configure("Accent.TButton", background=ACCENT, foreground="white", padding=(14, 8), font=(FONT_FAMILY, 10, "bold"))
    style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#9fb8cc")])

    style.configure("Danger.TButton", background=DANGER, foreground="white", padding=(14, 8), font=(FONT_FAMILY, 10, "bold"))
    style.map("Danger.TButton", background=[("active", DANGER_DARK), ("disabled", "#d9a9a3")])

    style.configure("TButton", padding=(10, 6))

    style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG, foreground=TEXT, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background="#f4f6f9", foreground=TEXT_MUTED, font=(FONT_FAMILY, 9, "bold"))
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])

    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 8), font=(FONT_FAMILY, 10))
