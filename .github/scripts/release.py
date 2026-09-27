"""Plan and publish a release from a successful main CI run."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile


SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
CONVENTIONAL = re.compile(r"^[a-zA-Z][\w-]*(?:\([^\n)]*\))?(!)?:")


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def api(path, *args):
    return json.loads(run("gh", "api", path, *args))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def releases(repo):
    result = []
    for page in api(f"repos/{repo}/releases", "--paginate", "--slurp"):
        result.extend(page)
    return result


def latest(release_list):
    stable = [
        item
        for item in release_list
        if not item["draft"] and not item["prerelease"] and VERSION.fullmatch(item["tag_name"])
    ]
    return max(
        stable,
        key=lambda item: tuple(map(int, VERSION.fullmatch(item["tag_name"]).groups())),
        default=None,
    )


def version_bump(messages):
    bump = 0
    for message in messages:
        header = message.lstrip("\n").split("\n", 1)[0]
        match = CONVENTIONAL.match(header)
        if (match and match.group(1)) or re.search(r"(?im)^BREAKING[ -]CHANGE:\s*\S", message):
            return 2
        if re.match(r"^feat(?:\([^\n)]*\))?:", header):
            bump = 1
    return bump


def git_commit(ref):
    return run("git", "rev-parse", "--verify", f"{ref}^{{commit}}")


def same_main(sha):
    return api(f"repos/{os.environ['REPOSITORY']}/git/ref/heads/main")["object"]["sha"] == sha


def checked_tag(tag, sha):
    refs = api(f"repos/{os.environ['REPOSITORY']}/git/matching-refs/tags/{tag}")
    exact = [ref for ref in refs if ref["ref"] == f"refs/tags/{tag}"]
    if exact:
        obj = exact[0]["object"]
        if obj["type"] == "tag":
            obj = api(f"repos/{os.environ['REPOSITORY']}/git/tags/{obj['sha']}")["object"]
        require(obj["type"] == "commit" and obj["sha"] == sha, "Tag points to a different commit")
        return True
    return False


def plan():
    repo = os.environ["REPOSITORY"]
    sha = os.environ["TARGET_SHA"]
    require(SHA.fullmatch(sha) and git_commit("HEAD") == sha, "Unexpected checkout")
    if not same_main(sha):
        print("Superseded main run; skipping")
        return
    deadline = time.monotonic() + 600
    while True:
        require(same_main(sha), "Main advanced while waiting for CodeQL")
        runs = api(
            f"repos/{repo}/actions/workflows/codeql.yml/runs",
            "--method",
            "GET",
            "-f",
            "event=push",
            "-f",
            "head_sha=" + sha,
            "-f",
            "per_page=100",
        )["workflow_runs"]
        if any(r["status"] == "completed" and r["conclusion"] == "success" for r in runs):
            break
        require(
            not any(r["status"] == "completed" for r in runs),
            "CodeQL push run failed; rerun CI after CodeQL succeeds",
        )
        require(time.monotonic() < deadline, "Timed out waiting for CodeQL push run")
        time.sleep(min(15, max(0, deadline - time.monotonic())))
    base = latest(releases(repo))
    require(base is not None, "No published stable release to anchor version")
    tag = base["tag_name"]
    run("git", "fetch", "--no-tags", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    base_sha = git_commit(f"refs/tags/{tag}")
    require(run("git", "merge-base", base_sha, sha) == base_sha, "Released tag is not an ancestor")
    if base_sha == sha:
        print("Already released")
        return
    messages = run("git", "log", "--format=%B%x00", f"{tag}..{sha}").split("\x00")
    major, minor, patch = map(int, VERSION.fullmatch(tag).groups())
    bump = version_bump(messages)
    if bump == 2:
        next_tag = f"v{major + 1}.0.0"
    elif bump == 1:
        next_tag = f"v{major}.{minor + 1}.0"
    else:
        next_tag = f"v{major}.{minor}.{patch + 1}"
    checked_tag(next_tag, sha)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"tag={next_tag}\nsha={sha}\n")


def assets_for(tag):
    zip_name = f"Screamer-{tag}-windows-x64.zip"
    folder = Path("release-assets")
    require(
        sorted(p.name for p in folder.iterdir()) == sorted([zip_name, zip_name + ".sha256"]),
        "Unexpected release assets",
    )
    archive, checksum = folder / zip_name, folder / (zip_name + ".sha256")
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    require(
        checksum.read_text(encoding="ascii").strip() == f"{digest}  {zip_name}",
        "Checksum mismatch",
    )
    with zipfile.ZipFile(archive) as z:
        require("Screamer/Screamer.exe" in z.namelist(), "Executable missing from archive")
    with checksum.open("rb") as source:
        sha_digest = hashlib.file_digest(source, "sha256").hexdigest()
    return archive, checksum, {zip_name: digest, checksum.name: sha_digest}


def verify_assets(release, digests):
    actual = {a["name"]: a["digest"] for a in release["assets"]}
    require(
        actual == {name: f"sha256:{digest}" for name, digest in digests.items()},
        "Release assets missing or have incorrect digests",
    )


def publish():
    repo, sha, tag = (os.environ[key] for key in ("REPOSITORY", "TARGET_SHA", "TAG"))
    require(
        SHA.fullmatch(sha) and VERSION.fullmatch(tag) and git_commit("HEAD") == sha,
        "Invalid release target",
    )
    archive, checksum, digests = assets_for(tag)
    require(same_main(sha), "Main advanced since release plan")
    all_releases = releases(repo)
    existing = next((r for r in all_releases if r["tag_name"] == tag), None)
    latest_release = latest(all_releases)
    require(latest_release is not None, "No published stable release")
    latest_version = tuple(map(int, VERSION.fullmatch(latest_release["tag_name"]).groups()))
    target_version = tuple(map(int, VERSION.fullmatch(tag).groups()))
    require(target_version >= latest_version, "A newer stable release already exists")
    tag_exists = checked_tag(tag, sha)
    if existing:
        require(existing["target_commitish"] == sha, "Existing release target mismatch")
        if not existing["draft"]:
            require(tag_exists, "Published release tag missing")
            verify_assets(existing, digests)
            return
        require(
            all(a["name"] in digests for a in existing["assets"]),
            "Unexpected assets on draft",
        )
        for asset in existing["assets"]:
            require(
                asset["digest"] == f"sha256:{digests[asset['name']]}",
                "Draft asset digest mismatch",
            )
        release_id = existing["id"]
    else:
        require(target_version > latest_version, "Release tag already published")
    if not tag_exists:
        run(
            "gh",
            "api",
            f"repos/{repo}/git/refs",
            "--method",
            "POST",
            "-f",
            f"ref=refs/tags/{tag}",
            "-f",
            f"sha={sha}",
        )
    require(checked_tag(tag, sha), "Tag does not point at tested SHA")
    if not existing:
        created = api(
            f"repos/{repo}/releases",
            "--method",
            "POST",
            "-f",
            f"tag_name={tag}",
            "-f",
            f"target_commitish={sha}",
            "-F",
            "draft=true",
            "-F",
            "generate_release_notes=true",
        )
        require(
            created["tag_name"] == tag and created["draft"] and created["target_commitish"] == sha,
            "New draft target mismatch",
        )
        release_id = created["id"]
    current = api(f"repos/{repo}/releases/{release_id}")
    require(current["draft"] and current["target_commitish"] == sha, "Draft changed")
    present = {asset["name"] for asset in current["assets"]}
    for path in (archive, checksum):
        if path.name not in present:
            run("gh", "release", "upload", tag, str(path), "--repo", repo)
    current = api(f"repos/{repo}/releases/{release_id}")
    require(current["draft"] and current["target_commitish"] == sha, "Draft changed")
    verify_assets(current, digests)
    require(same_main(sha), "Main advanced before publication")
    run("gh", "release", "edit", tag, "--repo", repo, "--draft=false")


if __name__ == "__main__":
    if sys.argv[1:] == ["plan"]:
        plan()
    elif sys.argv[1:] == ["publish"]:
        publish()
    else:
        raise SystemExit("Usage: release.py plan|publish")
