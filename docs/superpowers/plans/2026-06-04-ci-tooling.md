# CI Tooling Implementation Plan (ruff, permissions, pip-audit, CodeQL)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen CI with linting/formatting (ruff), least-privilege workflow permissions, supply-chain CVE scanning (pip-audit), and static security analysis (CodeQL).

**Architecture:** Add a `ruff.toml` config + one-time `ruff format` of the codebase, then extend `ci.yml` with a top-level read-only `permissions` block and two new jobs (`lint`, `audit`), and add a separate `codeql.yml` workflow. No runtime/`requirements.txt` changes — ruff/pip-audit are installed inside CI jobs only.

**Tech Stack:** GitHub Actions, ruff 0.15.15, pip-audit, `github/codeql-action` v3, Python 3.12 runners.

---

## Context (verified locally)

- `ruff check src/ tests/` → **6 findings**: 4 unused-import (`F401`, auto-fixable), 2 unused-local (`F841`).
- `F841` at `src/main.py:495` (`tray_app`) is **load-bearing** — the binding keeps the tray app alive during `app.exec()`; it gets `# noqa`, not deletion.
- `ruff format --check --line-length 100` → **11 files** would reformat (line-length 100 chosen to match existing style; default 88 reformats 14).
- `pip-audit -r requirements.txt` → **clean** (no known vulns). `requirements-build.txt` only fails to resolve locally because this dev venv is Python 3.14 and `pyinstaller==6.14.2` caps `<3.14`; it resolves on the CI runner's Python 3.12 (the release workflow installs it there).
- Existing `ci.yml`: jobs `compile` (ubuntu) + `test` (windows matrix 3.11/3.12); no top-level `permissions`. `release.yml` already sets `permissions: contents: write`.

## File Structure

- Create: `ruff.toml` — lint/format config.
- Modify: `src/audio.py`, `src/config.py`, `src/hotkey.py`, `src/settings_dialog.py` (remove unused imports); `src/main.py` (`# noqa` on the live binding); `tests/test_tray_menu.py` (drop unused local). Plus whole-repo `ruff format`.
- Modify: `.github/workflows/ci.yml` — top-level permissions + `lint` + `audit` jobs.
- Create: `.github/workflows/codeql.yml`.

⚠️ **Open-PR interaction:** PR #13 (custom hotkeys) rewrites `hotkey.py`, `config.py`, `settings_dialog.py`, `main.py`. The whole-repo `ruff format` here WILL conflict with #13. Resolution: merge whichever lands second will need a re-format / conflict resolution. Recommended order — merge #13 first, then rebase this branch and re-run `ruff format`. This is an accepted cost of adopting a formatter mid-flight.

---

## Task 1: ruff config

**Files:**
- Create: `ruff.toml`

- [ ] **Step 1: Write `ruff.toml`**

```toml
# Ruff config — lint (pyflakes/pycodestyle defaults) + formatter.
line-length = 100
target-version = "py311"
```

- [ ] **Step 2: Verify ruff picks up the config**

Run: `.venv/Scripts/python.exe -m ruff check src/ tests/`
Expected: still reports the 6 findings (config doesn't change them yet), and runs without a config error.

- [ ] **Step 3: Commit**

```bash
git add ruff.toml
git commit -m "build: add ruff config (lint + format, line-length 100)"
```

---

## Task 2: Fix ruff lint findings

**Files:**
- Modify: `src/audio.py:7`, `src/config.py:12`, `src/hotkey.py:42`, `src/settings_dialog.py:13` (auto), `src/main.py:495`, `tests/test_tray_menu.py:14` (manual)

- [ ] **Step 1a: Guard — confirm `config.APP_NAME` is not imported elsewhere**

`struct`/`ctypes.wintypes`/`Qt` are third-party imports nobody re-imports from these modules, but `APP_NAME` is a project symbol. Confirm nothing imports it FROM `config` (it's also exported by `utils`, which is the real source):

Run: `grep -rn "from src.config import" src tests | grep APP_NAME; grep -rn "config.APP_NAME" src tests`
Expected: no output (safe to drop the unused re-import from `config.py`).

- [ ] **Step 1b: Auto-fix the 4 unused imports**

Run: `.venv/Scripts/python.exe -m ruff check --fix src/ tests/`
This removes: `struct` (audio.py), `APP_NAME` (config.py), `ctypes.wintypes` (hotkey.py), `Qt` (settings_dialog.py).
Expected after: `2 errors` remain (the two `F841`).

- [ ] **Step 2: Fix `src/main.py:495` with a noqa (binding must stay alive)**

The `tray_app` reference keeps the tray app from being garbage-collected during `app.exec()`. Do NOT delete it. Change:

```python
    tray_app = _TrayApp(startup_mode=args.startup)
```
to:
```python
    tray_app = _TrayApp(startup_mode=args.startup)  # noqa: F841 — keep ref alive for app lifetime
```

- [ ] **Step 3: Fix `tests/test_tray_menu.py:14` (drop the unused binding)**

The QApplication singleton persists in Qt once constructed; the local name is unused. Change:

```python
    app = QApplication.instance() or QApplication([])
```
to:
```python
    QApplication.instance() or QApplication([])
```

- [ ] **Step 4: Verify lint is clean**

Run: `.venv/Scripts/python.exe -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 5: Verify tests still pass**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests`
Expected: OK (no regressions from import removals).

- [ ] **Step 6: Commit**

```bash
git add src/ tests/test_tray_menu.py
git commit -m "style: remove unused imports and silence load-bearing unused binding"
```

---

## Task 3: Apply ruff format

**Files:**
- Modify: whole repo (11 files reformatted under line-length 100)

- [ ] **Step 1: Apply the formatter**

Run: `.venv/Scripts/python.exe -m ruff format src/ tests/`
Expected: "11 files reformatted, 8 files left unchanged" (counts may shift slightly after Task 2 edits).

- [ ] **Step 2: Verify format is clean and tests pass**

Run: `.venv/Scripts/python.exe -m ruff format --check src/ tests/` → expected "N files already formatted".
Run: `.venv/Scripts/python.exe -m ruff check src/ tests/` → expected "All checks passed!".
Run: `.venv/Scripts/python.exe -m unittest discover -s tests` → expected OK.

- [ ] **Step 3: Commit**

```bash
git add src/ tests/
git commit -m "style: apply ruff format across the codebase"
```

---

## Task 4: ci.yml — permissions + lint + audit jobs

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a top-level least-privilege permissions block**

In `.github/workflows/ci.yml`, after the `concurrency:` block (before `jobs:`), insert:

```yaml
permissions:
  contents: read
```

- [ ] **Step 2: Add the `lint` job**

Append to the `jobs:` map (after the `test` job):

```yaml
  lint:
    name: lint (ruff)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff==0.15.15
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/
```

- [ ] **Step 3: Add the `audit` job**

Append after `lint`:

```yaml
  audit:
    name: pip-audit (CVE scan)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pip-audit
      - run: pip-audit -r requirements.txt -r requirements-build.txt
```

- [ ] **Step 4: Validate the workflow YAML**

Run:
```bash
.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8')); print('ci.yml OK')"
```
Expected: `ci.yml OK`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add ruff lint, format check, and pip-audit jobs with read-only permissions"
```

---

## Task 5: CodeQL workflow

**Files:**
- Create: `.github/workflows/codeql.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: CodeQL

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: "31 4 * * 1"   # weekly, Monday 04:31 UTC

permissions:
  contents: read
  security-events: write

jobs:
  analyze:
    name: analyze (python)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: python
      - uses: github/codeql-action/analyze@v3
        with:
          category: "/language:python"
```

- [ ] **Step 2: Validate the workflow YAML**

Run:
```bash
.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/codeql.yml', encoding='utf-8')); print('codeql.yml OK')"
```
Expected: `codeql.yml OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/codeql.yml
git commit -m "ci: add CodeQL static analysis workflow for Python"
```

---

## Task 6: Document the plan

**Files:**
- Create: `docs/superpowers/plans/2026-06-04-ci-tooling.md` (this file)

- [ ] **Step 1: Commit**

```bash
git add docs/superpowers/plans/2026-06-04-ci-tooling.md
git commit -m "docs: add CI tooling plan"
```

---

## Notes / Decisions baked in

- **ruff pinned to 0.15.15** in CI for reproducibility (matches the version validated locally). It is not a `requirements` manifest, so Dependabot won't bump it — update manually when desired.
- **`ruff format` enforced** (`--check` in CI) after a one-time whole-repo reformat. Line-length 100 keeps the diff smaller and matches existing style.
- **pip-audit runs on Python 3.12** so the `pyinstaller` pin resolves (the dev venv is 3.14, where it doesn't). Strict (no `continue-on-error`) — requirements are currently clean, so the gate is green; a future CVE will correctly block until the dep is bumped or the advisory is `--ignore-vuln`'d.
- **CodeQL** is a separate workflow (convention), weekly + on push/PR to main, with `security-events: write`. `github/codeql-action` is kept current by the github-actions Dependabot entry.
- **Least privilege:** `ci.yml` and `codeql.yml` declare minimal `permissions`; `release.yml` already had `contents: write`.
- **No new runtime dependencies.** ruff/pip-audit are CI-only installs.
