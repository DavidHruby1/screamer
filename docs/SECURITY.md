# Repository Security

This project is public. Visitors can read and fork it, but cannot push to this
repository without an authorized credential. Only the owner currently has write
access. GitHub account security, authorized apps and the owner's computers remain
part of the trust boundary; repository settings cannot protect a compromised admin.
Use 2FA with a passkey or security key and review authorized apps and SSH keys.

## Branch protection and administrator access

`main` requires a pull request, one approving review, dismissal of stale approvals,
resolved conversations, an up-to-date branch and the following GitHub Actions checks:

- `compile (ubuntu / py3.12)`
- `test (windows / py3.11)`
- `test (windows / py3.12)`
- `build (windows / py3.12)`
- `lint (ruff)`
- `pip-audit (CVE scan)`
- `analyze (python)`
- `dependency-review`

Required checks are tied to the GitHub Actions app. CODEOWNERS assigns workflow,
release and dependency changes to `@DavidHruby1`; applicable PRs require code owner
review. Force pushes and branch deletion are disabled in the branch rule.

**Administrator bypass is intentional and must remain enabled.** The owner can merge
their own PRs without another reviewer. Do not enable `enforce_admins`, remove this
exception through a ruleset, or add an environment approval that prevents the owner
from publishing. All checks should still be inspected before an admin merge.

## GitHub Actions

- All external fork contributors require approval before their workflows run.
  Inspect changes to workflows, dependency files and executable scripts before
  approving. Approval permits code execution on GitHub's temporary runner.
- The repository allows GitHub-owned actions only and requires full commit SHA pins.
  Every action in the workflow files has an upstream SHA with a version comment.
  Dependabot maintains these pins through reviewed PRs.
- The default token is read-only and cannot approve PRs. Checkouts do not persist
  credentials. CodeQL receives only its additional security reporting permission;
  only the release publication job receives `contents: write`.
- Jobs have explicit time limits. There are no self-hosted runners or stored
  repository Actions secrets.
- Dependency review rejects newly introduced dependencies with known vulnerabilities
  of low severity or higher. `pip-audit` also checks the resolved Python dependency
  set. These checks do not prove that an unknown vulnerability or malicious package
  is absent.

When adding an action, review its upstream source and permissions, pin the full SHA,
and keep the version comment so Dependabot can update it. An older open PR containing
unpinned actions needs to incorporate the updated `main` before its checks can rerun
under the SHA policy. Administrator merge access remains available.

## Alerts and releases

Dependabot alerts and security update PRs are enabled independently of weekly version
update PRs. Secret scanning and push protection remain enabled. Review and remediate
alerts; an empty alert list is not proof that no secret or vulnerability exists.
Private vulnerability reporting is enabled; see [Security Policy](../SECURITY.md).

Future releases are immutable after publication. The release workflow builds from a
successful CI push to this repository's `main`, waits for CodeQL on the same commit,
and gives build jobs no write token. It uploads and checks assets while the release
is a draft, then publishes it. Immutability does not retroactively lock old releases.
See [Release Operations](RELEASES.md) for retry and recovery procedures.

GitHub repository policies are configured through repository settings and are not
applied by checking out these files. After changing them, read the settings back and
confirm the administrator exception is still present. Do not grant extra token
permissions or disable security checks just to make a dependency update pass.
