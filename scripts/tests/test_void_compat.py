from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import void_compat as compat


def package(requires=(), provides=(), depends=(), virtual=()):
    return {"shlib-requires": list(requires), "shlib-provides": list(provides),
            "run_depends": list(depends), "provides": list(virtual)}


def void_index():
    return {
        "glibc": package(provides=["libc.so.6"]),
        "qt6-core": package(requires=["libc.so.6"], provides=["libQt6Core.so.6"]),
        "hyprutils": package(provides=["libhyprutils.so.10"]),
        "hyprpicker": package(requires=["libhyprutils.so.10"]),
        "pipewire": package(virtual=["libjack-1.0_1"]),
    }


def published_index():
    return {
        "darkly": package(requires=["libc.so.6", "libQt6Core.so.6"], depends=["qt6-core>=6.11.1_1", "glibc>=2.41_1"]),
        "hyprutils": package(requires=["libc.so.6"], provides=["libhyprutils.so.13"]),
        "hyprpicker": package(requires=["libhyprutils.so.13"], depends=["hyprutils-0.14.4_1"]),
    }


def write_index(path, packages):
    data = plistlib.dumps(packages)
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("index.plist")
        member.size = len(data)
        archive.addfile(member, BytesIO(data))


class DependencyNameTests(unittest.TestCase):
    def test_patterns(self):
        for pattern, name in [("qt6-core>=6.11.1_1", "qt6-core"), ("glibc<3", "glibc"),
                              ("foo>=1<2", "foo"), ("hyprutils-0.14.4_1", "hyprutils"),
                              ("libjack-1.0_1", "libjack"), ("kf6-frameworkintegration>=0", "kf6-frameworkintegration")]:
            with self.subTest(pattern=pattern):
                self.assertEqual(compat.dependency_name(pattern), name)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.void = void_index()
        self.published = published_index()

    def check(self):
        return [(problem.package, problem.detail) for problem in compat.check_compatibility(self.published, self.void)]

    def test_compatible_repositories_have_no_problems(self):
        self.assertEqual(self.check(), [])

    def test_soname_bump_in_void_is_reported(self):
        self.void["qt6-core"]["shlib-provides"] = ["libQt6Core.so.7"]
        self.assertEqual(self.check(), [("darkly", "links against libQt6Core.so.6, which no package provides")])

    def test_removed_void_dependency_is_reported(self):
        del self.void["qt6-core"]
        problems = self.check()
        self.assertIn(("darkly", "depends on qt6-core>=6.11.1_1, which no package provides"), problems)

    def test_virtual_packages_satisfy_dependencies(self):
        self.published["darkly"]["run_depends"].append("libjack>=0")
        self.assertEqual(self.check(), [])

    def test_replaced_void_package_provides_nothing(self):
        # Only Void's hyprutils provided so.10, and the published hyprutils replaces it.
        self.published["darkly"]["shlib-requires"].append("libhyprutils.so.10")
        self.assertIn(("darkly", "links against libhyprutils.so.10, which no package provides"), self.check())

    def test_void_consumer_of_a_replaced_library_is_reported(self):
        self.void["hyprlock"] = package(requires=["libhyprutils.so.10"])
        self.assertEqual(self.check(), [("hyprutils", "replaces Void's libhyprutils.so.10, which Void's hyprlock still links against")])

    def test_replaced_library_still_provided_elsewhere_is_not_reported(self):
        self.void["hyprlock"] = package(requires=["libhyprutils.so.10"])
        self.void["hyprutils-compat"] = package(provides=["libhyprutils.so.10"])
        self.assertEqual(self.check(), [])


TEMPLATE = """# Template file for 'darkly'
pkgname=darkly
version=0.5.45
revision=1
short_desc="Modern style"
distfiles="https://github.com/Bali10050/Darkly/archive/v${version}.tar.gz"

darkly-qt5_package() {
	short_desc+=" - Qt5 style"
}
"""


class BumpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "darkly").mkdir()
        self.template = self.root / "darkly" / "template"
        self.template.write_text(TEMPLATE)
        self.published = {
            "darkly": {"pkgver": "darkly-0.5.45_1", "sourcepkg": "darkly", **package(requires=["libQt6Core.so.6"])},
            "darkly-qt5": {"pkgver": "darkly-qt5-0.5.45_1", "sourcepkg": "darkly", **package(requires=["libQt5Core.so.5"])},
        }

    def tearDown(self):
        self.directory.cleanup()

    def bump(self, *problems):
        return compat.bump_revisions(list(problems), self.published, self.root)

    def test_missing_libraries_bump_the_source_template_once(self):
        bumped, unhandled = self.bump(
            compat.Problem("darkly", "soname", "links against libQt6Core.so.6, which no package provides"),
            compat.Problem("darkly-qt5", "soname", "links against libQt5Core.so.5, which no package provides"),
        )
        self.assertEqual(unhandled, [])
        self.assertEqual(list(bumped), ["darkly"])
        self.assertEqual(bumped["darkly"][0], "darkly-0.5.45_1")
        self.assertEqual(len(bumped["darkly"][1]), 2)
        self.assertEqual(self.template.read_text(), TEMPLATE.replace("revision=1", "revision=2"))

    def test_pending_rebuild_is_not_bumped_again(self):
        self.template.write_text(TEMPLATE.replace("revision=1", "revision=2"))
        bumped, unhandled = self.bump(compat.Problem("darkly", "soname", "links against libQt6Core.so.6, which no package provides"))
        self.assertEqual(bumped, {})
        self.assertIn("template 0.5.45_2 is awaiting a rebuild", unhandled[0].detail)
        self.assertIn("revision=2", self.template.read_text())

    def test_dependencies_of_a_bumped_package_ride_along(self):
        soname = compat.Problem("darkly", "soname", "links against libQt6Core.so.6, which no package provides")
        dependency = compat.Problem("darkly-qt5", "dependency", "depends on qt6-core>=6, which no package provides")
        bumped, unhandled = self.bump(soname, dependency)
        self.assertEqual(unhandled, [])
        self.assertEqual(bumped["darkly"][1], [soname, dependency])

    def test_dependency_and_replacement_problems_need_manual_fixes(self):
        problems = [compat.Problem("darkly", "dependency", "depends on qt6-core>=6, which no package provides"),
                    compat.Problem("darkly", "replaced", "replaces Void's libfoo.so.1, which Void's foo still links against")]
        bumped, unhandled = self.bump(*problems)
        self.assertEqual((bumped, unhandled), ({}, problems))
        self.assertEqual(self.template.read_text(), TEMPLATE)

    def test_commit_records_each_bump(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True).stdout
        git("init", "-q")
        git("-c", "user.name=test", "-c", "user.email=test@example.org", "commit", "-q", "--allow-empty", "-m", "init")
        git("add", ".")
        git("-c", "user.name=test", "-c", "user.email=test@example.org", "commit", "-q", "-m", "add darkly")
        problem = compat.Problem("darkly-qt5", "soname", "links against libQt5Core.so.5, which no package provides")
        bumped, _ = self.bump(problem)
        with patch.dict("os.environ", {"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.org",
                                       "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.org"}):
            compat.commit_bumps(bumped, self.root)
        message = git("log", "-1", "--format=%B")
        self.assertTrue(message.startswith("darkly: rebuild for current Void libraries\n\ndarkly-0.5.45_1 needs libraries"))
        self.assertIn("- darkly-qt5 links against libQt5Core.so.5", message)
        self.assertEqual(git("status", "--porcelain"), "")


class CommandTests(unittest.TestCase):
    def run_cli(self, published, void):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hinnom").mkdir()
            (root / "void").mkdir()
            write_index(root / "hinnom" / "x86_64-repodata", published)
            write_index(root / "void" / "x86_64-repodata", void)
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(StringIO()):
                code = compat.main(["--repository", str(root / "hinnom"), "--void-repository", str(root / "void")])
            return code, output.getvalue()

    def test_exit_codes(self):
        code, output = self.run_cli(published_index(), void_index())
        self.assertEqual(code, 0)
        self.assertIn("3 published packages are compatible", output)

        void = void_index()
        del void["glibc"]
        code, output = self.run_cli(published_index(), void)
        self.assertEqual(code, 1)
        self.assertIn("darkly: links against libc.so.6", output)

    def test_unreadable_index_fails(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(StringIO()):
            self.assertEqual(compat.main(["--repository", directory, "--void-repository", directory]), 2)


if __name__ == "__main__":
    unittest.main()
