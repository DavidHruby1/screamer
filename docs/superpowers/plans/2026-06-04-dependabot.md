# Dependabot Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable automated dependency update PRs for the project's two dependency surfaces — Python (`pip`) and GitHub Actions — via a `.github/dependabot.yml` config.

**Architecture:** A single declarative `dependabot.yml` (schema version 2) with two `updates` entries. Both run weekly, group minor+patch bumps into one PR per ecosystem (to cut noise), and leave major bumps as individual PRs (so breaking changes get individual review). No code changes; verification is YAML-parse + structural assertion.

**Tech Stack:** GitHub Dependabot config (YAML v2). Ecosystems: `pip` (root `requirements.txt` + `requirements-build.txt`), `github-actions` (`.github/workflows/*`).

---

## Context (verified in repo)

- Dependency files: `requirements.txt`, `requirements-build.txt` (both at repo root `/`).
- Workflows: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, using `actions/checkout@v4`, `actions/setup-python@v5` (versioned → Dependabot has targets).
- Default branch: `main`. No existing `.github/dependabot.yml`.
- Schema confirmed via GitHub Docs (Context7): `version: 2`; `groups` supports `patterns` / `update-types`; `commit-message` supports `prefix` + `include: "scope"`.

## File Structure

- Create: `.github/dependabot.yml` — the only artifact.

---

## Task 1: Create `dependabot.yml`

**Files:**
- Create: `.github/dependabot.yml`

- [ ] **Step 1: Write the config**

Create `.github/dependabot.yml` with exactly:

```yaml
# Dependabot configuration — automated dependency update PRs.
# Docs: https://docs.github.com/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file
version: 2
updates:
  # Python dependencies (requirements.txt + requirements-build.txt at root).
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "06:00"
      timezone: "Etc/UTC"
    open-pull-requests-limit: 5
    commit-message:
      prefix: "chore"
      include: "scope"
    labels:
      - "dependencies"
      - "python"
    groups:
      python-minor-and-patch:
        update-types:
          - "minor"
          - "patch"

  # GitHub Actions used by the CI and release workflows.
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "06:00"
      timezone: "Etc/UTC"
    open-pull-requests-limit: 5
    commit-message:
      prefix: "ci"
      include: "scope"
    labels:
      - "dependencies"
      - "github-actions"
    groups:
      actions-minor-and-patch:
        update-types:
          - "minor"
          - "patch"
```

- [ ] **Step 2: Verify YAML parses and has the expected structure**

Run (works whether or not PyYAML is installed — falls back to a structural string check):

```bash
.venv/Scripts/python.exe - <<'PY'
import pathlib, sys
text = pathlib.Path(".github/dependabot.yml").read_text(encoding="utf-8")
try:
    import yaml
    data = yaml.safe_load(text)
    assert data["version"] == 2, "version must be 2"
    ecos = {u["package-ecosystem"] for u in data["updates"]}
    assert ecos == {"pip", "github-actions"}, f"unexpected ecosystems: {ecos}"
    for u in data["updates"]:
        assert u["directory"] == "/"
        assert u["schedule"]["interval"] == "weekly"
        assert "groups" in u
    print("dependabot.yml OK (parsed via PyYAML):", sorted(ecos))
except ModuleNotFoundError:
    for token in ('version: 2', 'package-ecosystem: "pip"',
                  'package-ecosystem: "github-actions"', 'interval: "weekly"'):
        assert token in text, f"missing: {token}"
    print("dependabot.yml OK (string check; PyYAML not installed)")
PY
```
Expected: prints an `OK` line, no `AssertionError`.

- [ ] **Step 3: Confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m compileall src/ tests/` and `.venv/Scripts/python.exe -m unittest discover -s tests`
Expected: compile OK; tests still pass (config change touches no Python).

- [ ] **Step 4: Commit**

```bash
git add .github/dependabot.yml
git commit -m "ci: add Dependabot config for pip and github-actions"
```

---

## Task 2: Document the plan

**Files:**
- Create: `docs/superpowers/plans/2026-06-04-dependabot.md` (this file)

- [ ] **Step 1: Commit the plan**

```bash
git add docs/superpowers/plans/2026-06-04-dependabot.md
git commit -m "docs: add Dependabot setup plan"
```

---

## Notes / Decisions baked in

- **Two ecosystems only.** The repo has no Docker/npm/submodules, so `pip` + `github-actions` cover everything.
- **One `pip` entry covers both requirements files** — Dependabot scans the directory and updates the requirements `.txt` files it finds there; no separate entry needed.
- **Weekly, grouped minor+patch.** Reduces PR churn for a solo-maintainer repo. Major bumps stay ungrouped so breaking changes are reviewed one at a time.
- **Commit prefixes match the repo's Conventional Commits style:** `chore(deps):` for Python, `ci(deps):` for Actions (`include: "scope"` appends the `deps` scope).
- **Labels** `dependencies` + per-ecosystem. Dependabot auto-creates only the default `dependencies` label; custom labels (`python`, `github-actions`) that don't already exist in the repo are silently ignored (non-fatal). Create them with `gh label create python` / `gh label create github-actions` if you want them applied.
- **No target-branch override** — defaults to the repo default branch (`main`).
- **No tests added.** A static YAML config is verified by parse + structural assertion, not a unit test; GitHub validates the file server-side on push.
