# Plan — GitHub Release Pipeline

> **Superseded (2026-06):** This tag-triggered `release.yml` pipeline has been replaced by
> [Release Please](https://github.com/googleapis/release-please). The build/publish steps now live in
> `.github/workflows/release-please.yml` (run on release creation), and `release.yml` has been removed.
> See `docs/superpowers/plans/2026-06-04-release-please.md`. Kept below for historical context.

## Goal
On a version tag push, automatically build the Windows `.exe`, package it, and publish a GitHub Release with the artifact attached. No manual build/upload steps.

## Scope
- New workflow file: `.github/workflows/release.yml`.
- Trigger: push of a tag matching `v*` (e.g. `v1.0.0`, `v1.2.0-rc1`).
- Build on `windows-latest` (PyInstaller produces a Windows-native onedir bundle; cannot cross-build).
- Gate the release on the test suite passing first (don't ship a broken build).
- Package `dist\Screamer\` (onedir output) into a versioned zip.
- Publish a GitHub Release using the built-in `gh` CLI (no third-party action — matches project's minimal-dependency ethos).
- Auto-generate release notes from commit history.
- Mark pre-releases automatically when the tag contains a hyphen (`-rc`, `-beta`, etc.).
- Attach a SHA256 checksum file alongside the zip.

Out of scope: code signing (separate concern, needs a cert), auto-bumping version numbers, changelog curation, multi-arch.

## Key facts grounding the design
- `screamer.spec` uses `COLLECT` → output is a **directory** `dist\Screamer\` containing `Screamer.exe` + Qt DLLs. Not a single file. So the artifact must be a **zip of the folder**.
- `build_windows.ps1` creates its own `.venv`. In CI that's wasteful — install deps into the runner's Python directly and call PyInstaller, mirroring the build script's pip + pyinstaller steps.
- Existing `ci.yml` already runs the unittest suite on `windows-latest` with `QT_QPA_PLATFORM=offscreen`. The release workflow reuses that exact invocation as a gate.
- Build deps: `requirements.txt` + `requirements-build.txt` (pins `pyinstaller==6.14.2`).
- Python 3.12 is the target per `docs/PLAN.md`.

## Workflow design (`.github/workflows/release.yml`)

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: write   # required for gh release create

jobs:
  release:
    name: build and publish (windows / py3.12)
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt -r requirements-build.txt

      - name: Run tests (gate)
        env:
          QT_QPA_PLATFORM: offscreen
        run: python -m unittest discover -s tests -v

      - name: Build executable
        run: python -m PyInstaller --noconfirm --clean screamer.spec

      - name: Package
        shell: pwsh
        run: |
          $tag = $env:GITHUB_REF_NAME
          $zip = "Screamer-$tag-windows-x64.zip"
          Compress-Archive -Path dist\Screamer -DestinationPath $zip
          $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
          "$hash  $zip" | Out-File -FilePath "$zip.sha256" -Encoding ascii
          "ASSET_ZIP=$zip"        | Out-File -FilePath $env:GITHUB_ENV -Append
          "ASSET_SHA=$zip.sha256" | Out-File -FilePath $env:GITHUB_ENV -Append

      - name: Publish release
        env:
          GH_TOKEN: ${{ github.token }}
        shell: pwsh
        run: |
          $tag = $env:GITHUB_REF_NAME
          $ghArgs = @(
            "release", "create", $tag,
            $env:ASSET_ZIP, $env:ASSET_SHA,
            "--title", $tag,
            "--generate-notes"
          )
          if ($tag -match "-") { $ghArgs += "--prerelease" }
          & gh @ghArgs
```

> Review fix: the earlier draft passed `$pre` (possibly an empty string) as a trailing
> positional argument to `gh release create`. On a stable tag (no hyphen) that empty
> string would be parsed as an extra asset path with an empty name and the publish would
> fail. Building an args array and only appending `--prerelease` when needed avoids this.

## Steps to implement
1. Create `.github/workflows/release.yml` with the workflow above.
2. Validate YAML syntax locally (Python `yaml.safe_load`).
3. Document the release process in `README.md` (a short "Releases" section: tag `vX.Y.Z`, push tag, CI builds and publishes).
4. Verify nothing else references release artifacts inconsistently.

## Verification
- YAML parses without error.
- Logic walk-through: tag `v1.0.0` → tests run → PyInstaller builds `dist\Screamer\` → zip created → `gh release create` publishes with notes, non-prerelease. Tag `v1.0.0-rc1` → same but `--prerelease`.
- Cannot fully run end-to-end without pushing a real tag; that is left to the user. The workflow is validated by syntax + the fact that its build/test steps mirror the already-green `ci.yml` and `build_windows.ps1`.

## Risks
- PyInstaller build time on CI (~several min) — acceptable, runs only on tags.
- `gh release create` fails if a release for the tag already exists — acceptable (re-tag or delete release to retry).
- If tests are flaky on CI, releases block — desired behavior (gate).

## Commit split
1. `ci: add release workflow triggered on version tags`
2. `docs: document the release/tagging process in README`
