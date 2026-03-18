#!/usr/bin/env python3
"""Static import checks for core architecture dependency invariants."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

LAYER_RULES: dict[str, set[str]] = {
    "openreview_scraper.__main__": {"openreview_scraper.cli"},
    "openreview_scraper.cli": {
        "openreview_scraper.db",
        "openreview_scraper.models",
        "openreview_scraper.openreview",
        "openreview_scraper.service",
        "openreview_scraper.settings",
        "openreview_scraper.storage",
        "openreview_scraper.worker",
    },
    "openreview_scraper.openreview": {
        "openreview_scraper.models",
        "openreview_scraper.settings",
    },
    "openreview_scraper.db": {"openreview_scraper.settings"},
    "openreview_scraper.models": set(),
    "openreview_scraper.settings": set(),
    "openreview_scraper.storage": {"openreview_scraper.settings"},
    "openreview_scraper.service": {
        "openreview_scraper.db",
        "openreview_scraper.models",
        "openreview_scraper.openreview",
        "openreview_scraper.settings",
        "openreview_scraper.storage",
    },
    "openreview_scraper.worker": {
        "openreview_scraper.db",
        "openreview_scraper.service",
        "openreview_scraper.settings",
        "openreview_scraper.storage",
    },
}


def _module_path(module: str) -> Path:
    return (SRC / Path(*module.split("."))).with_suffix(".py")


def _internal_imports(module: str, path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    deps: set[str] = set()
    current_package = module.rsplit(".", 1)[0]

    def _record(candidate: str) -> None:
        if candidate in LAYER_RULES:
            deps.add(candidate)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "openreview_scraper" or alias.name.startswith("openreview_scraper."):
                    parts = alias.name.split(".")
                    if len(parts) >= 2:
                        _record(".".join(parts[:2]))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                if node.module:
                    _record(f"{current_package}.{node.module.split('.', 1)[0]}")
                else:
                    for alias in node.names:
                        _record(f"{current_package}.{alias.name.split('.', 1)[0]}")
            elif node.module == "openreview_scraper":
                for alias in node.names:
                    _record(f"openreview_scraper.{alias.name.split('.', 1)[0]}")
            elif node.module and node.module.startswith("openreview_scraper."):
                _record(".".join(node.module.split(".")[:2]))

    return deps


def main() -> int:
    errors: list[str] = []

    for module, allowed_deps in LAYER_RULES.items():
        module_file = _module_path(module)
        if not module_file.exists():
            continue

        for dep in sorted(_internal_imports(module, module_file)):
            if dep == module:
                continue
            if dep not in allowed_deps:
                errors.append(
                    f"{module_file.relative_to(ROOT)} imports {dep}, "
                    f"but allowed deps are: {sorted(allowed_deps)}"
                )

    if errors:
        print("Architecture checks failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("Architecture checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
