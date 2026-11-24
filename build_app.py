#!/usr/bin/env python3
"""Build a standalone FoamCutter executable from the current checkout."""

from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".build-venv"
if sys.platform == "win32":
    PYTHON = VENV / "Scripts" / "python.exe"
else:
    PYTHON = VENV / "bin" / "python"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    if not VENV.exists():
        venv.create(VENV, with_pip=True)

    run(str(PYTHON), "-m", "pip", "install", "--upgrade", "pip")
    run(str(PYTHON), "-m", "pip", "install", "-e", ".[cam,dxf,sim]")
    run(str(PYTHON), "-m", "pip", "install", "pyinstaller")

    separator = ";" if sys.platform == "win32" else ":"
    run(
        str(PYTHON),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "FoamCutter",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT),
        "--add-data",
        f"{ROOT / 'examples'}{separator}examples",
        str(ROOT / "slicer.pyw"),
    )

    print()
    print("Build complete.")
    if sys.platform == "darwin":
        print("Application:", ROOT / "dist" / "FoamCutter.app")
    elif sys.platform == "win32":
        print("Executable:", ROOT / "dist" / "FoamCutter" / "FoamCutter.exe")
    else:
        print("Executable:", ROOT / "dist" / "FoamCutter" / "FoamCutter")


if __name__ == "__main__":
    main()