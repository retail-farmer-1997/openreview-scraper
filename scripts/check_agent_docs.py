#!/usr/bin/env python3
"""Structural checks for agent docs, links, and execution-plan hygiene."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "agent-harness.json"
DEFAULT_CONFIG = {
    "agent_docs": {
        "max_agents_lines": 160,
        "required_files": [
            "AGENTS.md",
            "ARCHITECTURE.md",
            "agent-harness.json",
            "docs/architecture.md",
            "docs/agent-ops/index.md",
            "docs/agent-ops/core-beliefs.md",
            "docs/agent-ops/plans.md",
            "docs/agent-ops/pr-loop.md",
            "docs/agent-ops/architecture-invariants.md",
            "docs/agent-ops/quality-score.md",
            "docs/agent-ops/testing.md",
            "docs/agent-ops/reliability.md",
            "docs/agent-ops/security.md",
            "docs/exec-plans/tech-debt-tracker.md",
        ],
        "required_agent_links": [
            "ARCHITECTURE.md",
            "docs/agent-ops/index.md",
            "docs/agent-ops/pr-loop.md",
            "docs/exec-plans/tech-debt-tracker.md",
        ],
        "metadata_files": [
            "docs/architecture.md",
            "docs/agent-ops/index.md",
            "docs/agent-ops/core-beliefs.md",
            "docs/agent-ops/plans.md",
            "docs/agent-ops/pr-loop.md",
            "docs/agent-ops/architecture-invariants.md",
            "docs/agent-ops/quality-score.md",
            "docs/agent-ops/testing.md",
            "docs/agent-ops/reliability.md",
            "docs/agent-ops/security.md",
            "docs/exec-plans/tech-debt-tracker.md",
        ],
        "link_check_files": [
            "AGENTS.md",
            "ARCHITECTURE.md",
            "docs/architecture.md",
            "docs/agent-ops/index.md",
            "docs/agent-ops/core-beliefs.md",
            "docs/agent-ops/plans.md",
            "docs/agent-ops/pr-loop.md",
            "docs/agent-ops/architecture-invariants.md",
            "docs/agent-ops/quality-score.md",
            "docs/agent-ops/testing.md",
            "docs/agent-ops/reliability.md",
            "docs/agent-ops/security.md",
            "docs/exec-plans/tech-debt-tracker.md",
        ],
        "required_metadata_keys": ["Owner:", "Last Reviewed:", "Status:"],
    },
    "plans": {
        "active_dir": "docs/exec-plans/active",
        "completed_dir": "docs/exec-plans/completed",
        "required_sections": ["Objective", "Scope", "Risks", "Validation", "Status Log"],
    },
}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
MD_HEADING_RE = r"^#{{1,6}}\s+{section}\s*$"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _is_external_link(link: str) -> bool:
    prefixes = ("http://", "https://", "mailto:", "tel:")
    return link.startswith(prefixes)


def _resolve_link(current_file: Path, link: str) -> Path | None:
    target = link.split("#", 1)[0].split("?", 1)[0].strip()
    if not target:
        return None
    if target.startswith("/"):
        return ROOT / target.lstrip("/")
    return (current_file.parent / target).resolve()


def _resolve_rel_paths(paths: list[str]) -> list[Path]:
    return [ROOT / rel_path for rel_path in paths]


def load_config() -> dict[str, dict[str, object]]:
    if not CONFIG_PATH.exists():
        return {
            "agent_docs": dict(DEFAULT_CONFIG["agent_docs"]),
            "plans": dict(DEFAULT_CONFIG["plans"]),
        }

    loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config = {
        "agent_docs": dict(DEFAULT_CONFIG["agent_docs"]),
        "plans": dict(DEFAULT_CONFIG["plans"]),
    }
    for section in ("agent_docs", "plans"):
        config[section].update(loaded.get(section, {}))
    return config


def check_required_files(errors: list[str], required_files: list[Path]) -> None:
    for path in required_files:
        if not path.exists():
            errors.append(f"missing file: {_rel(path)}")


def check_agents_length_and_links(
    errors: list[str],
    required_links: list[str],
    max_agents_lines: int,
) -> None:
    agents = ROOT / "AGENTS.md"
    if not agents.exists():
        return

    agents_text = agents.read_text(encoding="utf-8")
    line_count = len(agents_text.splitlines())
    if line_count > max_agents_lines:
        errors.append(f"AGENTS.md too long: {line_count} lines (limit {max_agents_lines})")

    for link in required_links:
        if link not in agents_text:
            errors.append(f"AGENTS.md missing required link: {link}")


def check_metadata(errors: list[str], metadata_paths: list[Path], required_metadata_keys: list[str]) -> None:
    for path in metadata_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for key in required_metadata_keys:
            if key not in text:
                errors.append(f"{_rel(path)} missing metadata key: {key}")


def check_internal_links(errors: list[str], markdown_paths: list[Path]) -> None:
    for path in markdown_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for link in LINK_RE.findall(text):
            if _is_external_link(link):
                continue
            resolved = _resolve_link(path, link)
            if resolved is None:
                continue
            if not resolved.exists():
                errors.append(
                    f"broken link in {_rel(path)} -> {link} (resolved to {_rel(resolved)})"
                )


def check_exec_plan_structure(
    errors: list[str],
    active_plan_dir: Path,
    completed_plan_dir: Path,
    required_sections: list[str],
) -> None:
    if not active_plan_dir.exists():
        errors.append(f"missing directory: {_rel(active_plan_dir)}")
    if not completed_plan_dir.exists():
        errors.append(f"missing directory: {_rel(completed_plan_dir)}")

    for plan_dir in (active_plan_dir, completed_plan_dir):
        if not plan_dir.exists():
            continue
        for path in sorted(plan_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for section in required_sections:
                pattern = re.compile(
                    MD_HEADING_RE.format(section=re.escape(section)),
                    re.IGNORECASE | re.MULTILINE,
                )
                if not pattern.search(text):
                    errors.append(f"{_rel(path)} missing section heading: {section}")


def main() -> int:
    errors: list[str] = []
    config = load_config()
    agent_docs = config["agent_docs"]
    plans = config["plans"]

    check_required_files(errors, _resolve_rel_paths(agent_docs["required_files"]))
    check_agents_length_and_links(
        errors,
        agent_docs["required_agent_links"],
        agent_docs["max_agents_lines"],
    )
    check_metadata(
        errors,
        _resolve_rel_paths(agent_docs["metadata_files"]),
        agent_docs["required_metadata_keys"],
    )
    check_internal_links(errors, _resolve_rel_paths(agent_docs["link_check_files"]))
    check_exec_plan_structure(
        errors,
        ROOT / plans["active_dir"],
        ROOT / plans["completed_dir"],
        plans["required_sections"],
    )

    if errors:
        print("Agent docs checks failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("Agent docs checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
