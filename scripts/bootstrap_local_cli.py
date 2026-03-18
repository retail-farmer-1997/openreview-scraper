#!/usr/bin/env python3
"""Bootstrap a repo-local virtualenv and dispatch to repo modules."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"
STAMP_PATH = VENV_DIR / ".bootstrap-stamp"
ENTRYPOINT_TO_MODULE = {
    "openreview-scraper": "openreview_scraper",
}


def _bin_dir() -> Path:
    return VENV_DIR / ("Scripts" if os.name == "nt" else "bin")


def venv_python() -> Path:
    name = "python.exe" if os.name == "nt" else "python"
    return _bin_dir() / name


def venv_python_usable() -> bool:
    python = venv_python()
    if not python.exists():
        return False

    completed = subprocess.run(
        [str(python), "-c", "import sys; print(sys.executable)"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def bootstrap_reason(entrypoint: str) -> str | None:
    if entrypoint not in ENTRYPOINT_TO_MODULE:
        raise ValueError(f"unsupported entrypoint: {entrypoint}")
    if not venv_python().exists():
        return "missing-venv"
    if not venv_python_usable():
        return "broken-venv"
    if not STAMP_PATH.exists():
        return "missing-stamp"

    stamp_mtime = STAMP_PATH.stat().st_mtime
    for path in (ROOT / "pyproject.toml",):
        if path.exists() and path.stat().st_mtime > stamp_mtime:
            return f"stale-bootstrap:{path.name}"
    return None


def ensure_venv() -> None:
    if venv_python_usable():
        return

    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)

    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], cwd=ROOT, check=True)


def run_bootstrap(reason: str) -> None:
    ensure_venv()
    STAMP_PATH.write_text("bootstrapped\n", encoding="utf-8")


def dispatch(entrypoint: str, argv: list[str]) -> int:
    reason = bootstrap_reason(entrypoint)
    if reason is not None:
        print(f"Bootstrapping local CLI environment ({reason})...", file=sys.stderr)
        run_bootstrap(reason)

    env = dict(os.environ)
    src_path = str(ROOT / "src")
    existing = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"

    command = [str(venv_python()), "-m", ENTRYPOINT_TO_MODULE[entrypoint], *argv]
    completed = subprocess.run(command, cwd=ROOT, env=env)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: bootstrap_local_cli.py <entrypoint> [args...]", file=sys.stderr)
        return 2

    entrypoint = args[0]
    return dispatch(entrypoint, args[1:])


if __name__ == "__main__":
    sys.exit(main())
