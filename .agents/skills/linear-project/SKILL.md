---
name: linear-project
description: Manage `openreview-scraper` through a repo-specific Linear + GitHub workflow only. Use when work touches Linear issues, milestones, project descriptions, release planning, dependency management, branch/PR linkage, or when any Notion-style memory workflow needs to be replaced with repo docs and execution plans.
---

# Linear Project

## Overview

Use this skill to manage `openreview-scraper` with Linear for planning and GitHub for execution
evidence. Do not use Notion for this repo; keep durable operating knowledge in `AGENTS.md`,
`docs/agent-ops/*`, and `docs/exec-plans/*`.

## Load First

- `AGENTS.md`
- `docs/agent-ops/index.md`
- `docs/agent-ops/pr-loop.md`
- `docs/exec-plans/tech-debt-tracker.md`
- The relevant active execution plan for the work
- [references/project-map.md](references/project-map.md)
- [references/issue-patterns.md](references/issue-patterns.md)

## Workflow

1. Read the repo docs and the live Linear project state before assuming current priorities or open work.
2. Keep project planning in Linear: create or update issues, milestones, dependencies, project descriptions, and status there.
3. Keep implementation evidence in GitHub: branches, PRs, validation output, review notes, and merge history belong there.
4. Keep durable decisions in-repo: update `docs/exec-plans/active/*.md` while work is in flight and update `AGENTS.md` or other docs when the operating model changes.
5. Rewrite any lingering Notion-oriented text in Linear or repo docs instead of layering new instructions on top.

## Rules

- Keep this repo on the `Retail-farmer` team and the `OpenReview Scraper` project unless the user explicitly changes ownership.
- Start non-trivial implementation work from a Linear issue or an explicitly named execution plan.
- Use parent/child issues for decomposition and `blockedBy` only for real ordering constraints.
- Update the live Linear project description when the repo operating model changes materially.
- Do not write project-management state to Notion or another external memory system for this repo.

## GitHub Contract

- Prefer the Linear-provided `gitBranchName` when available on an issue.
- Otherwise include the Linear issue key in the branch name.
- Start PR titles with the issue key.
- Include the Linear key in the PR body with `Closes RET-xx` or `Refs RET-xx`.
- Put validation commands and risk notes in the PR body.

## References

- [references/project-map.md](references/project-map.md) for stable IDs, URLs, and milestone meanings.
- [references/issue-patterns.md](references/issue-patterns.md) for status flow, issue decomposition, and the issue-to-PR contract.
