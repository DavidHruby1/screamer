"""Release pipeline rules, without requiring GitHub credentials or a Windows runner."""

import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "release.py"
spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

SHA = "a" * 40
REPO = "owner/repo"


class VersionTests(unittest.TestCase):
    def test_releases_includes_all_paginated_pages(self):
        pages = [
            [{"tag_name": f"v2.0.{n}", "draft": True, "prerelease": False} for n in range(30)],
            [{"tag_name": "v1.0.4", "draft": False, "prerelease": False}],
        ]
        with patch.object(release, "api", return_value=pages):
            self.assertEqual(release.latest(release.releases(REPO))["tag_name"], "v1.0.4")

    def test_versions_start_at_highest_published_stable_release(self):
        items = [
            {"tag_name": "v1.0.4", "draft": False, "prerelease": False},
            {"tag_name": "v2.0.0", "draft": True, "prerelease": False},
            {"tag_name": "v1.9.0-rc1", "draft": False, "prerelease": True},
            {"tag_name": "v1.0.2", "draft": False, "prerelease": False},
        ]
        self.assertEqual(release.latest(items)["tag_name"], "v1.0.4")

    def test_breaking_feature_and_other_commits(self):
        self.assertEqual(release.version_bump(["chore: update", "fix: repair"]), 0)
        self.assertEqual(release.version_bump(["feat(ui): add recording pill"]), 1)
        self.assertEqual(release.version_bump(["feat!: change contract"]), 2)
        self.assertEqual(release.version_bump(["fix: repair\n\nBREAKING CHANGE: new format"]), 2)
        self.assertEqual(release.version_bump(["fix: repair", "\nfeat: later feature"]), 1)
        self.assertEqual(release.version_bump(["fix: repair", "\nfeat!: later break"]), 2)


class PlanTests(unittest.TestCase):
    def test_plans_minor_from_published_version_after_codeql_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp, "output")
            with patch.dict(os.environ, REPOSITORY=REPO, TARGET_SHA=SHA, GITHUB_OUTPUT=str(output)):

                def github(path, *args):
                    if path.endswith("/git/ref/heads/main"):
                        return {"object": {"sha": SHA}}
                    if path.endswith("/runs"):
                        return {"workflow_runs": [{"status": "completed", "conclusion": "success"}]}
                    if path.endswith("/releases"):
                        return [[{"tag_name": "v1.0.4", "draft": False, "prerelease": False}]]
                    if "/git/matching-refs/tags/" in path:
                        return []
                    raise AssertionError(path)

                def git(*args):
                    if args[:3] == ("git", "rev-parse", "--verify"):
                        return SHA if "HEAD" in args[3] else "b" * 40
                    if args[:2] == ("git", "merge-base"):
                        return "b" * 40
                    if args[:2] == ("git", "log"):
                        return "feat: add feature\x00"
                    if args[:2] == ("git", "fetch"):
                        return ""
                    raise AssertionError(args)

                with (
                    patch.object(release, "api", side_effect=github),
                    patch.object(release, "run", side_effect=git),
                ):
                    release.plan()
            self.assertEqual(output.read_text(encoding="utf-8"), f"tag=v1.1.0\nsha={SHA}\n")


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        assets = Path("release-assets")
        assets.mkdir()
        self.archive = assets / "Screamer-v1.0.5-windows-x64.zip"
        with zipfile.ZipFile(self.archive, "w") as zipped:
            zipped.writestr("Screamer/Screamer.exe", b"exe")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum = assets / (self.archive.name + ".sha256")
        self.checksum.write_text(f"{digest}  {self.archive.name}\n", encoding="ascii")
        self.digests = {
            self.archive.name: digest,
            self.checksum.name: hashlib.sha256(self.checksum.read_bytes()).hexdigest(),
        }
        self.env = patch.dict(os.environ, REPOSITORY=REPO, TARGET_SHA=SHA, TAG="v1.0.5")
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_invalid_checksum_blocks_publication(self):
        self.checksum.write_text("incorrect", encoding="ascii")
        with patch.object(release, "run", return_value=SHA) as command:
            with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                release.publish()
        command.assert_called_once_with("git", "rev-parse", "--verify", "HEAD^{commit}")

    def test_missing_executable_blocks_publication(self):
        with zipfile.ZipFile(self.archive, "w") as zipped:
            zipped.writestr("other.txt", b"none")
        self.checksum.write_text(
            f"{hashlib.sha256(self.archive.read_bytes()).hexdigest()}  {self.archive.name}\n",
            encoding="ascii",
        )
        with patch.object(release, "run", return_value=SHA) as command:
            with self.assertRaisesRegex(RuntimeError, "Executable missing"):
                release.publish()
        command.assert_called_once_with("git", "rev-parse", "--verify", "HEAD^{commit}")

    def test_published_release_with_mismatched_asset_is_not_changed(self):
        existing = {
            "tag_name": "v1.0.5",
            "target_commitish": SHA,
            "draft": False,
            "prerelease": False,
            "assets": [{"name": self.archive.name, "digest": "sha256:incorrect"}],
        }
        base = {"tag_name": "v1.0.4", "draft": False, "prerelease": False}
        with (
            patch.object(release, "run", return_value=SHA) as command,
            patch.object(release, "same_main", return_value=True),
            patch.object(release, "releases", return_value=[existing, base]),
            patch.object(release, "checked_tag", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "digests"):
                release.publish()
        command.assert_called_once()

    def test_draft_publishes_only_after_both_assets_are_verified(self):
        draft = {
            "tag_name": "v1.0.5",
            "target_commitish": SHA,
            "draft": True,
            "prerelease": False,
            "assets": [],
            "id": 42,
        }
        base = {"tag_name": "v1.0.4", "draft": False, "prerelease": False}
        uploaded = dict(
            draft,
            assets=[
                {"name": name, "digest": f"sha256:{digest}"}
                for name, digest in self.digests.items()
            ],
        )
        with (
            patch.object(release, "run", return_value=SHA) as command,
            patch.object(release, "same_main", return_value=True),
            patch.object(release, "releases", return_value=[draft, base]),
            patch.object(release, "checked_tag", return_value=True),
            patch.object(release, "api", side_effect=[draft, uploaded]),
        ):
            release.publish()

        calls = [call.args for call in command.call_args_list]
        self.assertEqual(
            calls[-1], ("gh", "release", "edit", "v1.0.5", "--repo", REPO, "--draft=false")
        )
        self.assertEqual(sum(args[:3] == ("gh", "release", "upload") for args in calls), 2)

    def test_orphaned_tag_is_reused_after_previous_draft_creation_failed(self):
        base = {"tag_name": "v1.0.4", "draft": False, "prerelease": False}
        draft = {
            "tag_name": "v1.0.5",
            "target_commitish": SHA,
            "draft": True,
            "prerelease": False,
            "assets": [],
            "id": 42,
        }
        uploaded = dict(
            draft,
            assets=[
                {"name": name, "digest": f"sha256:{digest}"}
                for name, digest in self.digests.items()
            ],
        )
        with (
            patch.object(release, "run", return_value=SHA) as command,
            patch.object(release, "same_main", return_value=True),
            patch.object(release, "releases", return_value=[base]) as listing,
            patch.object(release, "checked_tag", return_value=True),
            patch.object(release, "api", side_effect=[draft, draft, uploaded]) as api,
        ):
            release.publish()

        calls = [call.args for call in command.call_args_list]
        listing.assert_called_once_with(REPO)
        self.assertEqual(
            api.call_args_list[0].args[:3], (f"repos/{REPO}/releases", "--method", "POST")
        )
        self.assertEqual(api.call_args_list[1].args, (f"repos/{REPO}/releases/42",))
        self.assertFalse(any(args[:3] == ("gh", "release", "create") for args in calls))
        self.assertFalse(
            any(args[:4] == ("gh", "api", f"repos/{REPO}/git/refs", "--method") for args in calls)
        )


if __name__ == "__main__":
    unittest.main()
