# PyInstaller spec - builds a single self-contained VAGScan executable.
# Used by .github/workflows/build-app.yml for the Windows download, and
# works unchanged on Linux.
#
# Build locally with:  pyinstaller packaging/vagscan.spec
import os
import sys

sys.setrecursionlimit(5000)

project_root = os.path.abspath(os.getcwd())
src_root = os.path.join(project_root, "src")

# The fault-code knowledge base is data, not code - without this the app
# runs but knows nothing about any code it finds.
datas = [(os.path.join(src_root, "vagscan", "dtc_db", "data"), os.path.join("vagscan", "dtc_db", "data"))]

# pyserial picks its port-enumeration backend at runtime by platform, which
# static analysis doesn't follow; missing it breaks adapter auto-detection.
hiddenimports = [
    "serial.tools.list_ports",
    "serial.tools.list_ports_common",
    "serial.tools.list_ports_linux",
    "serial.tools.list_ports_osx",
    "serial.tools.list_ports_posix",
    "serial.tools.list_ports_windows",
    "serial.serialwin32",
    "serial.serialposix",
]

a = Analysis(
    [os.path.join(project_root, "packaging", "launcher.py")],
    pathex=[src_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "numpy", "matplotlib", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VAGScan",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # GUI app: no console window behind it
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
