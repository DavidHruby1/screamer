# Release Operations

Every reviewed merge to `main` triggers CI and CodeQL. After successful CI, the Release
workflow waits for CodeQL on that same commit, checks that commit is still the tip of
`main`, and plans the next version from the latest **published stable** release tag.
It uses the highest change since that tag: `BREAKING CHANGE:` or a Conventional Commit
`!` is major, `feat:` is minor, and other commits are patch. The starting point is
`v1.0.4`, not the older Release Please manifest. There is no release PR or changelog
file; GitHub generates the release notes.

The Windows job runs unit tests, builds the onedir executable, and produces the versioned
ZIP and `.sha256`. Only a separate publication job receives `contents: write`; it
checks the tested SHA, checksum, executable in the ZIP and GitHub asset digests, then
publishes the draft. It never runs dependency installation or the build with write
permission. A tag may exist before publication if asset upload fails; this is not a
public release. The workflow accepts a matching draft/tag on retry but refuses to
overwrite an asset or tag with a mismatched SHA/digest.

## Recovery

- If the release fails because CI finished before CodeQL, wait for CodeQL and rerun
  the successful **CI** workflow for that commit. A failed CodeQL run must be fixed
  before retrying. A newer merge supersedes the older release attempt.
- If the build, upload or publication fails, inspect the failed run and rerun it after
  the cause is fixed. It can resume a matching draft/tag; mismatched or manually
  edited artifacts require human investigation. Do not delete a published release or
  force-update its tag to make a check pass.
- A published release with a matching SHA and asset digests is treated as already
  complete. The release workflow will not overwrite it.

This is automation after **human review of the code PR**, not automated bypass of
branch protection. There is no signing certificate or supply-chain attestation: a
checksum detects transfer corruption but is not proof of provenance. CI checks the
build output exists; it does not run the packaged app with a real microphone. Windows
DPAPI, Win32 input and the downloadable ZIP should be smoke-tested on Windows when
shipping a significant desktop change.
