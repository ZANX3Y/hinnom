from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import plistlib
import sys
import tarfile
import tempfile
import unittest

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
        return compat.check_compatibility(self.published, self.void)

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
