from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import BytesIO, StringIO
import json
from pathlib import Path
import plistlib
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_catalog as catalog


class FakeGitHub:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        response = self.responses[endpoint]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def template(version="1.9", revision=1, commit=None):
    return {
        "name": "example", "version": version, "revision": revision,
        "package_version": f"{version}_{revision}", "commit": commit,
        "repository": "owner/example", "description": "Example package",
        "homepage": "https://example.org", "license": "MIT",
        "packages": {"example": "Example package", "example-devel": "Example headers"},
    }


def release_api(version="1.9"):
    return FakeGitHub({"repos/owner/example/releases/latest": {"tag_name": f"v{version}"}})


def metadata(name="example", version="1.9_1", arch="x86_64"):
    return {
        "pkgver": f"{name}-{version}", "architecture": arch,
        "sourcepkg": "example", "short_desc": f"Published {name}",
        "license": "MIT", "homepage": "https://example.org",
        "filename-size": 123, "installed_size": 456,
        "filename-sha256": "a" * 64, "run_depends": ["glibc>=2.41_1"],
    }


def write_index(path, packages, mode="w:zst"):
    data = plistlib.dumps(packages)
    with tarfile.open(path, mode) as archive:
        member = tarfile.TarInfo("index.plist")
        member.size = len(data)
        archive.addfile(member, BytesIO(data))


class TemplateTests(unittest.TestCase):
    def test_actual_templates_include_all_subpackages(self):
        templates = catalog.read_templates(catalog.ROOT / "srcpkgs")
        self.assertIn("darkly", templates)
        self.assertIn("darkly-qt5", templates["darkly"]["packages"])
        self.assertIn("development files", templates["hyprutils"]["packages"]["hyprutils-devel"])
        self.assertEqual(templates["hyprland"]["repository"], "hyprwm/Hyprland")

    def test_metadata_read_does_not_execute_template_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "example"
            package.mkdir()
            marker = root / "executed"
            (package / "template").write_text(
                f"pkgname=example\nversion=1.0\nrevision=1\n"
                f"touch {marker}\nshort_desc=\"An example # with apostrophe's\"\n"
                'distfiles="https://github.com/owner/example/archive/v${version}.tar.gz"\n'
            )
            result = catalog.read_templates(root)["example"]
            self.assertFalse(marker.exists())
            self.assertEqual(result["description"], "An example # with apostrophe's")
            self.assertEqual(result["repository"], "owner/example")
            (package / "template").write_text("pkgname=example\nversion=$(exit 1)\nrevision=1\n")
            with self.assertRaises(catalog.CatalogError):
                catalog.read_templates(root)


@unittest.skipUnless(shutil.which("xbps-uhelper"), "requires XBPS version comparison")
class UpdateTests(unittest.TestCase):
    def check(self, source=None, api=None, previous=None):
        source = source or template()
        return catalog.check_updates({"example": source}, api or release_api(), previous)

    def test_version_ordering_includes_prereleases_and_revisions(self):
        for left, right, expected in [
            ("1.10", "1.9", 1), ("1.0rc1", "1.0", -1),
            ("1.0_2", "1.0_1", 1), ("5.000", "5.000", 0),
        ]:
            with self.subTest(left=left, right=right):
                self.assertEqual(catalog.compare_versions(left, right), expected)

    def test_release_comparison_does_not_treat_older_releases_as_updates(self):
        for version, status in [("1.10", "outdated"), ("1.9", "current"), ("1.8", "ahead")]:
            with self.subTest(version=version):
                result = self.check(api=release_api(version))["packages"][0]
                self.assertEqual(result["status"], status)
                self.assertFalse(result["stale"])
                self.assertIsNotNone(result["last_success_at"])

    def test_tag_fallback_finds_highest_stable_version_across_pages(self):
        api = FakeGitHub({
            "repos/owner/example/releases/latest": catalog.GitHubError("not found", 404),
            "repos/owner/example/tags?per_page=100&page=1": [{"name": "v1.9"}] * 100,
            "repos/owner/example/tags?per_page=100&page=2": [
                {"name": "v1.8"}, {"name": "v2.0-rc1"}, {"name": "nightly"}, {"name": "v1.10"},
            ],
        })
        result = self.check(api=api)["packages"][0]
        self.assertEqual(result["latest"]["version"], "1.10")
        self.assertEqual(result["latest"]["kind"], "tag")
        self.assertEqual(result["status"], "outdated")

    def test_api_failure_does_not_fall_back_to_tags(self):
        for code in (403, 429, 500):
            with self.subTest(code=code):
                api = FakeGitHub({"repos/owner/example/releases/latest": catalog.GitHubError("unavailable", code)})
                result = self.check(api=api)["packages"][0]
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["latest"])
                self.assertEqual(len(api.calls), 1)

    def test_failed_check_preserves_last_success_across_repeated_failures(self):
        previous = self.check(api=release_api("1.10"))
        saved = previous["packages"][0]
        for _ in range(2):
            api = FakeGitHub({"repos/owner/example/releases/latest": catalog.GitHubError("rate limited", 403)})
            previous = self.check(api=api, previous=previous)
            result = previous["packages"][0]
            self.assertEqual(result["status"], "unknown")
            self.assertTrue(result["stale"])
            self.assertEqual(result["latest"], saved["latest"])
            self.assertEqual(result["last_success_at"], saved["last_success_at"])

    def test_previous_result_from_different_repository_is_not_reused(self):
        previous = self.check()
        previous["packages"][0]["repository"] = "someone/else"
        api = FakeGitHub({"repos/owner/example/releases/latest": catalog.GitHubError("unavailable", 500)})
        result = self.check(api=api, previous=previous)["packages"][0]
        self.assertFalse(result["stale"])
        self.assertIsNone(result["latest"])

    def test_full_commit_hashes_are_compared_even_when_prefixes_match(self):
        old = "abcdef0" + "1" * 33
        new = "abcdef0" + "2" * 33
        for comparison, expected in [("ahead", "outdated"), ("behind", "ahead"), ("diverged", "unknown")]:
            with self.subTest(comparison=comparison):
                api = FakeGitHub({
                    "repos/owner/example/commits/HEAD": {"sha": new},
                    f"repos/owner/example/compare/{old}...{new}": {"status": comparison},
                })
                result = self.check(template(commit=old), api)["packages"][0]
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(api.calls), 2)

    def test_equal_commit_needs_no_comparison_request(self):
        sha = "a" * 40
        api = FakeGitHub({"repos/owner/example/commits/HEAD": {"sha": sha}})
        result = self.check(template(commit=sha), api)["packages"][0]
        self.assertEqual(result["status"], "current")
        self.assertEqual(len(api.calls), 1)

    def test_uninterpretable_release_and_prerelease_are_unknown(self):
        for release in [{"tag_name": "release-next"}, {"tag_name": "v2.0rc1", "prerelease": True}, []]:
            with self.subTest(release=release):
                api = FakeGitHub({"repos/owner/example/releases/latest": release})
                self.assertEqual(self.check(api=api)["packages"][0]["status"], "unknown")

    def test_non_github_package_is_skipped_without_a_request(self):
        source = template()
        source["repository"] = None
        api = FakeGitHub({})
        self.assertEqual(self.check(source, api)["packages"][0]["status"], "skipped")
        self.assertEqual(api.calls, [])

    def test_cli_json_and_human_modes_keep_exit_codes(self):
        for version, expected in [("1.9", 0), ("1.10", 1), (None, 2)]:
            for json_mode in (True, False):
                with self.subTest(version=version, json=json_mode):
                    api = release_api(version) if version else FakeGitHub({
                        "repos/owner/example/releases/latest": catalog.GitHubError("failed", 500)
                    })
                    stdout = StringIO()
                    with patch.object(catalog, "read_templates", return_value={"example": template()}), patch.object(catalog, "GitHub", return_value=api), redirect_stdout(stdout), redirect_stderr(StringIO()):
                        exit_code = catalog.check_main(["--json"] if json_mode else [])
                    self.assertEqual(exit_code, expected)
                    if json_mode:
                        self.assertEqual(json.loads(stdout.getvalue())["packages"][0]["name"], "example")
                    else:
                        self.assertIn("example", stdout.getvalue())


@unittest.skipUnless(shutil.which("xbps-uhelper"), "requires XBPS version comparison")
class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.templates = {"example": template()}
        self.repositories = [{"architecture": "x86_64", "url": "https://repo.example/x86_64", "checked_at": catalog.timestamp()}]
        self.indexes = {"x86_64": {"example": metadata(), "example-devel": metadata("example-devel")}}
        self.updates = catalog.check_updates(self.templates, release_api())

    def generate(self):
        return catalog.generate_catalog(self.templates, self.repositories, self.indexes, self.updates)

    def test_published_subpackages_inherit_upstream_and_keep_real_metadata(self):
        result = self.generate()
        package = next(p for p in result["packages"] if p["name"] == "example-devel")
        self.assertEqual(package["source_package"], "example")
        self.assertEqual(package["description"], "Published example-devel")
        self.assertEqual(package["available_architectures"], ["x86_64"])
        self.assertEqual(package["status"], "current")
        self.assertEqual(package["upstream"]["latest"]["version"], "1.9")
        build = package["builds"][0]
        self.assertEqual(build["download_url"], "https://repo.example/x86_64/example-devel-1.9_1.x86_64.xbps")
        self.assertEqual(build["download_size"], 123)
        self.assertEqual(build["dependencies"], ["glibc>=2.41_1"])

    def test_template_revision_not_yet_published_is_awaiting_build(self):
        self.templates["example"] = template(revision=2)
        self.updates = catalog.check_updates(self.templates, release_api())
        result = self.generate()
        for package in result["packages"]:
            self.assertEqual(package["status"], "awaiting_build")
            self.assertEqual(package["template"]["package_version"], "1.9_2")
            self.assertEqual(package["builds"][0]["package_version"], "1.9_1")

    def test_new_upstream_release_marks_published_packages_outdated(self):
        self.updates = catalog.check_updates(self.templates, release_api("1.10"))
        self.assertTrue(all(p["status"] == "outdated" for p in self.generate()["packages"]))

    def test_builds_newer_than_checkout_are_compared_to_upstream_directly(self):
        self.indexes["x86_64"]["example"] = metadata(version="1.10_1")
        self.updates = catalog.check_updates(self.templates, release_api("1.10"))
        result = {p["name"]: p for p in self.generate()["packages"]}
        self.assertEqual(result["example"]["status"], "current")
        self.assertEqual(result["example-devel"]["status"], "outdated")

    def test_unpublished_packages_do_not_invent_architecture_availability(self):
        self.indexes["x86_64"].pop("example-devel")
        package = next(p for p in self.generate()["packages"] if p["name"] == "example-devel")
        self.assertEqual(package["status"], "unpublished")
        self.assertEqual(package["available_architectures"], [])
        self.assertEqual(package["description"], "Example headers")

    def test_per_architecture_versions_and_noarch_artifacts(self):
        self.repositories.append({"architecture": "aarch64", "url": "https://repo.example/aarch64"})
        self.indexes["aarch64"] = {"example": metadata(version="1.8_1", arch="noarch")}
        package = next(p for p in self.generate()["packages"] if p["name"] == "example")
        self.assertEqual(package["available_architectures"], ["x86_64", "aarch64"])
        self.assertEqual([b["status"] for b in package["builds"]], ["current", "awaiting_build"])
        self.assertEqual(package["status"], "awaiting_build")
        self.assertTrue(package["builds"][1]["download_url"].endswith("example-1.8_1.noarch.xbps"))

    def test_retired_but_published_package_remains_visible_as_unmanaged(self):
        self.indexes["x86_64"]["retired"] = metadata("retired")
        package = next(p for p in self.generate()["packages"] if p["name"] == "retired")
        self.assertEqual(package["status"], "unmanaged")
        self.assertIsNone(package["template"])
        self.assertIsNone(package["upstream"])

    def test_stale_upstream_data_is_not_presented_as_current(self):
        previous = self.generate()
        api = FakeGitHub({"repos/owner/example/releases/latest": catalog.GitHubError("failed", 500)})
        self.updates = catalog.check_updates(self.templates, api, previous)
        for package in self.generate()["packages"]:
            self.assertEqual(package["status"], "unknown")
            self.assertTrue(package["upstream"]["stale"])
            self.assertEqual(package["upstream"]["latest"]["version"], "1.9")

    def test_supplied_update_report_must_match_template(self):
        self.updates["packages"][0]["revision"] = 0
        with self.assertRaisesRegex(catalog.CatalogError, "does not match"):
            self.generate()

    def test_malformed_update_reports_are_rejected(self):
        for document in [[], {"schema_version": 1}, {"schema_version": 1, "packages": [None]}, {"schema_version": 999, "packages": []}]:
            with self.subTest(document=document):
                with self.assertRaises(catalog.CatalogError):
                    catalog.generate_catalog(self.templates, self.repositories, self.indexes, document)
        self.updates["packages"].append(deepcopy(self.updates["packages"][0]))
        with self.assertRaisesRegex(catalog.CatalogError, "Duplicate"):
            self.generate()

    def test_successful_report_without_upstream_metadata_is_rejected(self):
        self.updates["packages"][0]["latest"] = None
        with self.assertRaises(catalog.CatalogError):
            self.generate()

    def test_index_package_name_and_architecture_are_validated(self):
        for prop, value in [("pkgver", "someone-else-1.9_1"), ("architecture", "aarch64")]:
            with self.subTest(prop=prop):
                before = self.indexes["x86_64"]["example"][prop]
                self.indexes["x86_64"]["example"][prop] = value
                with self.assertRaises(catalog.CatalogError):
                    self.generate()
                self.indexes["x86_64"]["example"][prop] = before

    def test_real_compressed_index_and_offline_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "x86_64-repodata"
            write_index(index, self.indexes["x86_64"])
            updates = root / "updates.json"
            updates.write_text(json.dumps(self.updates))
            output = root / "packages.json"
            with patch.object(catalog, "read_templates", return_value=self.templates):
                exit_code = catalog.catalog_main(["--repository", f"x86_64={root}", "--updates", str(updates), "--output", str(output)])
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(json.loads(output.read_text())["packages"]), 2)

    def test_broken_index_does_not_replace_existing_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "broken"
            index.write_text("not an XBPS index")
            output = root / "packages.json"
            output.write_text("existing catalog")
            with patch.object(catalog, "read_templates", return_value=self.templates), redirect_stderr(StringIO()):
                code = catalog.catalog_main(["--repository", f"x86_64={index}", "--output", str(output)])
            self.assertEqual(code, 2)
            self.assertEqual(output.read_text(), "existing catalog")


if __name__ == "__main__":
    unittest.main()
