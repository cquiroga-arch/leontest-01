"""Post-build check: run the packaged executable and confirm it's intact.

Building successfully proves very little - PyInstaller will happily produce
an app that launches and then behaves as if it knows nothing about any
fault code, because the JSON data silently didn't get bundled. This runs
the real binary's self-test and fails the build if that happened.
"""
import subprocess
import sys
from pathlib import Path


def main(binary: str) -> int:
    path = Path(binary)
    if not path.is_file():
        print(f"FAIL: no se generó el ejecutable en {path}", file=sys.stderr)
        return 1

    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"Ejecutable: {path} ({size_mb:.1f} MB)")

    try:
        result = subprocess.run(
            [str(path), "--selftest", "--no-dialog"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: el autodiagnóstico no terminó a tiempo", file=sys.stderr)
        return 1

    print(result.stdout)
    if result.stderr.strip():
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0 or "RESULTADO: OK" not in result.stdout:
        print("FAIL: el autodiagnóstico del ejecutable no pasó", file=sys.stderr)
        return 1

    print("OK: el ejecutable arranca y su base de fallas está completa")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: verify_build.py <ruta-al-ejecutable>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
