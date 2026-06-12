# Release Please Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt [Release Please](https://github.com/googleapis/release-please) so version bumps, `CHANGELOG.md`, git tags, and GitHub releases are automated from Conventional Commits — and the existing PyInstaller Windows build attaches its zip to each automated release.

**Architecture:** A single `release-please.yml` workflow runs on every push to `main`. Job 1 (`release-please`, ubuntu) runs `googleapis/release-please-action@v4` which maintains a rolling "release PR" (version + changelog) and, when that PR is merged, creates the tag + GitHub release. Job 2 (`build`, windows) runs only when a release was created (`needs.release-please.outputs.release_created == 'true'`), checks out the new tag, builds the exe, and uploads the zip to the release with `gh release upload`. Keeping the build in the same workflow avoids the GITHUB_TOKEN limitation (tags/releases created with `GITHUB_TOKEN` do **not** trigger separate workflows) — so no PAT is needed. The old tag-triggered `release.yml` is removed.

**Tech Stack:** `googleapis/release-please-action@v4`, `release-type: simple`, manifest config, GitHub Actions, PyInstaller (Windows), `gh` CLI.

---

## Research summary (verified via Context7 + web, June 2026)

- **Action:** `googleapis/release-please-action@v4` (current; `google-github-actions/release-please-action` is archived). Outputs include `release_created`, `releases_created`, `tag_name`, `version`, `major/minor/patch`, `sha`, `upload_url`, `body`.
- **No-PAT integration:** the official README pattern uploads build artifacts in the **same** workflow, gated on `if: ${{ steps.release.outputs.release_created }}`, using `gh release upload <tag_name> <file>`. This is the documented way and sidesteps the "GITHUB_TOKEN events don't trigger workflows" rule that would otherwise stop a tag-triggered `release.yml`.
- **release-type `simple`:** for a repo with a `CHANGELOG.md` (+ optional `version.txt`); maintains the changelog and the manifest version. Fits this Python app, which has no `pyproject.toml`/`setup.py`.
- **Manifest bootstrap for an already-released repo:** seed `.release-please-manifest.json` with the current version so the next version is computed from commits since then (`{".": "1.0.1"}` — repo already has tags `v1.0.0`, `v1.0.1`).
- **Tag format:** root package (`include-component-in-tag: false`) tags as `v<version>` (e.g. `v1.0.2`), matching the existing `v*` tags.
- **First-release timing:** on `main`, the only commit since `v1.0.1` is a `refactor:` (no version bump under Conventional Commits), so a release PR will appear only once a `feat`/`fix` lands — expected behavior, not a bug.

Sources: googleapis/release-please-action README (Context7), googleapis/release-please docs (Context7), GitHub Actions token docs (web).

## File Structure

- Create: `release-please-config.json` — release-please configuration.
- Create: `.release-please-manifest.json` — seeded current version.
- Create: `.github/workflows/release-please.yml` — release-please job + windows build/upload job.
- Delete: `.github/workflows/release.yml` — replaced (tag trigger would no longer fire under release-please's GITHUB_TOKEN).
- Modify: `README.md` — rewrite the "Releases" section for the new flow.

Local verification is limited to JSON/YAML validity and parity of the build steps with the proven `release.yml`; the end-to-end release-PR/version-bump/build behavior is only observable on GitHub after this merges to `main` (noted in Task 4).

---

## Task 1: release-please config + manifest

**Files:**
- Create: `release-please-config.json`
- Create: `.release-please-manifest.json`

- [ ] **Step 1: Write `release-please-config.json`**

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "release-type": "simple",
  "include-component-in-tag": false,
  "packages": {
    ".": {}
  },
  "changelog-sections": [
    { "type": "feat", "section": "Features" },
    { "type": "fix", "section": "Bug Fixes" },
    { "type": "perf", "section": "Performance Improvements" },
    { "type": "revert", "section": "Reverts" },
    { "type": "docs", "section": "Documentation", "hidden": true },
    { "type": "ci", "section": "Continuous Integration", "hidden": true },
    { "type": "build", "section": "Build System", "hidden": true },
    { "type": "chore", "section": "Miscellaneous Chores", "hidden": true },
    { "type": "style", "section": "Styles", "hidden": true },
    { "type": "refactor", "section": "Code Refactoring", "hidden": true },
    { "type": "test", "section": "Tests", "hidden": true }
  ]
}
```

- [ ] **Step 2: Write `.release-please-manifest.json`**

```json
{
  ".": "1.0.1"
}
```

- [ ] **Step 3: Validate both files are valid JSON**

Run:
```bash
.venv/Scripts/python.exe -c "import json; c=json.load(open('release-please-config.json',encoding='utf-8')); m=json.load(open('.release-please-manifest.json',encoding='utf-8')); assert c['release-type']=='simple'; assert c['include-component-in-tag'] is False; assert m['.']=='1.0.1'; print('config+manifest OK')"
```
Expected: `config+manifest OK`.

- [ ] **Step 4: Commit**

```bash
git add release-please-config.json .release-please-manifest.json
git commit -m "ci: add release-please config and version manifest"
```

---

## Task 2: release-please workflow (+ remove old release.yml)

**Files:**
- Create: `.github/workflows/release-please.yml`
- Delete: `.github/workflows/release.yml`

- [ ] **Step 1: Write `.github/workflows/release-please.yml`**

```yaml
name: Release Please

on:
  push:
    branches: [main]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  release-please:
    name: release-please
    runs-on: ubuntu-latest
    outputs:
      release_created: ${{ steps.release.outputs.release_created }}
      tag_name: ${{ steps.release.outputs.tag_name }}
    steps:
      - uses: googleapis/release-please-action@v4
        id: release
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json

  build:
    name: build and publish (windows / py3.12)
    needs: release-please
    if: ${{ needs.release-please.outputs.release_created == 'true' }}
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ needs.release-please.outputs.tag_name }}

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt -r requirements-build.txt

      # Gate the published build on the same test suite CI runs.
      - name: Run tests
        env:
          QT_QPA_PLATFORM: offscreen
        run: python -m unittest discover -s tests -v

      - name: Build executable
        run: python -m PyInstaller --noconfirm --clean screamer.spec

      # screamer.spec uses COLLECT (onedir): zip the whole dist\Screamer folder.
      - name: Package
        shell: pwsh
        run: |
          $tag = "${{ needs.release-please.outputs.tag_name }}"
          $zip = "Screamer-$tag-windows-x64.zip"
          Compress-Archive -Path dist\Screamer -DestinationPath $zip
          $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
          "$hash  $zip" | Out-File -FilePath "$zip.sha256" -Encoding ascii
          "ASSET_ZIP=$zip"        | Out-File -FilePath $env:GITHUB_ENV -Append
          "ASSET_SHA=$zip.sha256" | Out-File -FilePath $env:GITHUB_ENV -Append

      # Attach the build to the release that release-please already created.
      - name: Upload assets to release
        env:
          GH_TOKEN: ${{ github.token }}
        shell: pwsh
        run: gh release upload "${{ needs.release-please.outputs.tag_name }}" $env:ASSET_ZIP $env:ASSET_SHA --clobber
```

- [ ] **Step 2: Delete the old tag-triggered release workflow**

```bash
git rm .github/workflows/release.yml
```

- [ ] **Step 3: Validate the new workflow YAML**

Run:
```bash
.venv/Scripts/python.exe -c "import yaml; w=yaml.safe_load(open('.github/workflows/release-please.yml',encoding='utf-8')); assert set(w['jobs'])=={'release-please','build'}; assert w['jobs']['build']['needs']=='release-please'; print('release-please.yml OK')"
```
Expected: `release-please.yml OK`.

- [ ] **Step 4: Confirm old workflow is gone**

Run: `test ! -f .github/workflows/release.yml && echo "release.yml removed"`
Expected: `release.yml removed`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release-please.yml
git commit -m "ci: replace tag-triggered release with release-please workflow"
```

---

## Task 3: Update README release docs

**Files:**
- Modify: `README.md` (the `## Releases` section)

- [ ] **Step 1: Replace the Releases section**

In `README.md`, replace the entire `## Releases` section (the block describing `git tag vX.Y.Z` / `git push origin vX.Y.Z` and the workflow steps) with:

```markdown
## Releases

Releases are automated with [Release Please](https://github.com/googleapis/release-please) from [Conventional Commits](https://www.conventionalcommits.org/).

1. Merging commits to `main` keeps a **Release PR** up to date — it bumps the version and updates `CHANGELOG.md`.
2. Merging that Release PR tags the version (`vX.Y.Z`) and publishes a GitHub Release with generated notes.
3. A Windows build job then runs the test suite, builds with PyInstaller, and attaches the packaged app:

```text
Screamer-vX.Y.Z-windows-x64.zip
Screamer-vX.Y.Z-windows-x64.zip.sha256
```

Version bumps follow the commit types: `fix:` → patch, `feat:` → minor, and `feat!:`/`BREAKING CHANGE:` → major.
```

- [ ] **Step 2: Verify the old tag instructions are gone**

Run: `grep -n "git tag v" README.md || echo "no manual tag instructions remain"`
Expected: `no manual tag instructions remain`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the release-please release flow"
```

---

## Task 4: Verification + plan doc

- [ ] **Step 1: Validate all new config/workflow files together**

Run:
```bash
.venv/Scripts/python.exe -c "import json,yaml; json.load(open('release-please-config.json',encoding='utf-8')); json.load(open('.release-please-manifest.json',encoding='utf-8')); yaml.safe_load(open('.github/workflows/release-please.yml',encoding='utf-8')); yaml.safe_load(open('.github/workflows/ci.yml',encoding='utf-8')); print('all config/workflows valid')"
```
Expected: `all config/workflows valid`.

- [ ] **Step 2: Confirm nothing else regressed**

Run: `.venv/Scripts/python.exe -m compileall -q src/ tests/ && .venv/Scripts/python.exe -m unittest discover -s tests 2>&1 | tail -3`
Expected: tests pass (this change touches no Python).

- [ ] **Step 3: Commit the plan**

```bash
git add docs/superpowers/plans/2026-06-04-release-please.md
git commit -m "docs: add release-please implementation plan"
```

---

## Post-merge verification (cannot be done locally — runs on GitHub)

After this merges to `main`:
1. The `release-please` job runs; once a `feat`/`fix` commit exists since `v1.0.1`, a **Release PR** appears (title like `chore(main): release 1.1.0`).
2. Merging the Release PR creates tag `v1.1.0` + a GitHub Release, then the `build` job uploads the zip + `.sha256`.
3. If the first Release PR includes more history than expected, anchor it by adding `"bootstrap-sha": "386f8d403e18727e23136f54a4110cbf3ef08c80"` (the `v1.0.1` commit) to `release-please-config.json`.

## Notes / Decisions baked in

- **No PAT:** build runs in the same workflow gated on `release_created`; `gh release upload` uses the default `GITHUB_TOKEN`. This is the documented pattern and avoids managing a secret.
- **`release.yml` removed:** a tag pushed by release-please (via `GITHUB_TOKEN`) would not trigger it, so it would silently never build. Its build steps are migrated verbatim into the `build` job.
- **`simple` release-type:** no `pyproject.toml`/`setup.py` to bump; release-please maintains `CHANGELOG.md` + the manifest. No `version.txt` is added (the build derives the version from `tag_name`).
- **Action pinned to `@v4`** (major) so the `github-actions` Dependabot entry can bump it.
- **Changelog hides** docs/ci/build/chore/style/refactor/test; surfaces feat/fix/perf/revert.
