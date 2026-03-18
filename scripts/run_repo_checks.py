#!/usr/bin/env python3
"""Canonical local/CI entrypoint for repository validation."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent


def _run(command: list[str]) -> None:
    rendered = " ".join(command)
    print(f"$ {rendered}")
    subprocess.run(command, cwd=ROOT, check=True)


def run_guardrails() -> None:
    _run([sys.executable, "scripts/check_agent_docs.py"])
    _run([sys.executable, "scripts/check_architecture.py"])


def run_tests() -> None:
    _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])


CHECKS: dict[str, Callable[[], None]] = {
    "guardrails": run_guardrails,
    "packaging": lambda: _run([sys.executable, "scripts/run_packaging_smoke.py"]),
    "tests": run_tests,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "checks",
        nargs="+",
        choices=[*CHECKS.keys(), "all"],
        help="Checks to run in order",
    )
    return parser.parse_args()


def expand_checks(requested: list[str]) -> list[str]:
    expanded: list[str] = []
    for check in requested:
        if check == "all":
            expanded.extend(["guardrails", "tests"])
        else:
            expanded.append(check)

    ordered: list[str] = []
    seen: set[str] = set()
    for check in expanded:
        if check not in seen:
            ordered.append(check)
            seen.add(check)
    return ordered


def main() -> int:
    args = parse_args()
    try:
        for check in expand_checks(args.checks):
            CHECKS[check]()
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
