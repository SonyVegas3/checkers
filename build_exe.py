"""Build ``CheckersBot.exe`` (the tkinter panel + the bot) with PyInstaller.

Usage
-----
    python build_exe.py                # one-file windowed exe  -> dist/CheckersBot.exe
    python build_exe.py --onedir       # faster start, a folder with the exe inside
    python build_exe.py --no-selftest  # skip the automatic smoke run

The exe bundles the whole ``playwright`` package (including its node driver), so
the machine that runs it does not need Python.  It does need a browser: either
Playwright's Chromium (``python -m playwright install chromium``) or an installed
Chrome/Edge, which the panel picks automatically (``--channel``).

Playwright is installed as:  python -m pip install pyinstaller
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

try:                       # a redirected stdout on Windows is cp1252, keep it safe
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
NAME = "CheckersBot"


def ensure_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        print("PyInstaller is missing. Install it with:")
        print("    python -m pip install pyinstaller")
        return False


def build(onefile: bool = True) -> Path:
    target = ROOT / "dist" / (f"{NAME}.exe" if onefile else Path(NAME) / f"{NAME}.exe")
    args = [
        str(ROOT / "gui.py"),
        "--name", NAME,
        "--noconfirm",
        "--clean",
        "--windowed",
        # the whole playwright package is needed: node driver + protocol files
        "--collect-all", "playwright",
        "--collect-all", "pyee",
        "--collect-all", "greenlet",
        "--hidden-import", "playwright.sync_api",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ]
    if onefile:
        args.append("--onefile")
    icon = ROOT / "icon.ico"
    if icon.exists():
        args += ["--icon", str(icon)]
    print("PyInstaller " + " ".join(args))
    subprocess.check_call([sys.executable, "-m", "PyInstaller", *args], cwd=str(ROOT))
    if not target.exists():
        raise SystemExit(f"build finished but {target} is missing")
    size = target.stat().st_size / (1024 * 1024)
    print(f"\nOK: {target}  ({size:.1f} MB)")
    return target


def smoke_test(target: Path) -> bool:
    """Run the exe in selftest mode: the window must open and close cleanly."""
    with tempfile.TemporaryDirectory() as folder:
        marker = Path(folder) / "selftest.txt"
        print(f"smoke test: {target.name} --selftest")
        try:
            subprocess.run([str(target), "--selftest", "--selftest-log", str(marker)],
                           timeout=180, check=False)
        except subprocess.TimeoutExpired:
            print("! the exe did not finish the selftest in time")
            return False
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == "ok":
            print("OK: smoke test passed (window created)")
            return True
        print("! smoke test failed - the window did not initialise")
        return False


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not ensure_pyinstaller():
        return 2
    onefile = "--onedir" not in argv
    target = build(onefile=onefile)
    if "--no-selftest" not in argv:
        if not smoke_test(target):
            return 1
    print("\nready: run  " + str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
