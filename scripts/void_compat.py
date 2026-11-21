"""Check that published packages still fit the current Void Linux repositories."""

import argparse
import re
import sys

from package_catalog import REPOSITORY_URL, CatalogError, read_index


VOID_URL = "https://repo-default.voidlinux.org/current"
VOID_REPOSITORIES = (VOID_URL, f"{VOID_URL}/nonfree")


def dependency_name(pattern):
    """Return the package name from an XBPS pattern: name>=1.0, name<2 or name-1.0_1."""
    name = re.match(r"[^<>=]*", pattern)[0]
    if name == pattern:
        name = re.sub(r"-[^-]+_\d+$", "", name)
    return name


def providers(index, skip=frozenset()):
    """Map sonames and package names, including virtual packages, to the package providing them."""
    sonames, names = {}, {}
    for name, package in index.items():
        if name in skip:
            continue
        names.setdefault(name, name)
        for virtual in package.get("provides", ()):
            names.setdefault(dependency_name(virtual), name)
        for soname in package.get("shlib-provides", ()):
            sonames.setdefault(soname, name)
    return sonames, names


def check_compatibility(published, void):
    """Return (package, problem) pairs for published packages that no longer fit Void.

    A published package that shares a name with a Void package replaces it, so
    the replaced Void package provides nothing. Void packages that still link
    against a library only the replaced package provided are reported too.
    """
    shadowed = published.keys() & void.keys()
    void_sonames, void_names = providers(void, shadowed)
    own_sonames, own_names = providers(published)
    sonames = void_sonames | own_sonames
    names = void_names | own_names

    problems = []
    for name, package in sorted(published.items()):
        for soname in package.get("shlib-requires", ()):
            if soname not in sonames:
                problems.append((name, f"links against {soname}, which no package provides"))
        for dependency in package.get("run_depends", ()):
            if dependency_name(dependency) not in names:
                problems.append((name, f"depends on {dependency}, which no package provides"))

    dropped = {}
    for name in shadowed:
        for soname in set(void[name].get("shlib-provides", ())) - set(published[name].get("shlib-provides", ())):
            if soname not in sonames:
                dropped[soname] = name
    for name, package in sorted(void.items()):
        if name in shadowed:
            continue
        for soname in package.get("shlib-requires", ()):
            if soname in dropped:
                problems.append((dropped[soname], f"replaces Void's {soname}, which Void's {name} still links against"))
    return problems


def index_location(location, arch):
    return location if location.endswith("-repodata") else f"{location.rstrip('/')}/{arch}-repodata"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check published packages against the current Void Linux repositories.")
    parser.add_argument("--arch", default="x86_64")
    parser.add_argument("--repository", metavar="URL_OR_PATH", help=f"published repository; default: {REPOSITORY_URL}/ARCH")
    parser.add_argument("--void-repository", action="append", metavar="URL_OR_PATH", help="repeat for each Void repository; default: current and current/nonfree")
    args = parser.parse_args(argv)
    try:
        published = read_index(index_location(args.repository or f"{REPOSITORY_URL}/{args.arch}", args.arch))
        void = {}
        for location in args.void_repository or VOID_REPOSITORIES:
            void |= read_index(index_location(location, args.arch))
    except CatalogError as exc:
        print(f"check-compat: {exc}", file=sys.stderr)
        return 2
    problems = check_compatibility(published, void)
    for name, problem in problems:
        print(f"{name}: {problem}")
    if problems:
        return 1
    print(f"{len(published)} published packages are compatible with {len(void)} Void packages")
    return 0
