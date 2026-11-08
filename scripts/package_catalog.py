"""Read package templates, check upstreams, and describe published XBPS packages."""

import argparse
from datetime import datetime, timezone
from functools import lru_cache
from io import BytesIO
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
SOURCE_URL = "https://github.com/ZANX3Y/hinnom"
REPOSITORY_URL = "https://hinnom.znx.pw"
SCHEMA_VERSION = 1


class CatalogError(Exception):
    pass


class GitHubError(CatalogError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@lru_cache(maxsize=4096)
def compare_versions(left, right):
    """Return XBPS ordering, including prereleases and package revisions."""
    try:
        result = subprocess.run(
            ["xbps-uhelper", "cmpver", left, right], capture_output=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CatalogError("Version comparison requires xbps-uhelper (from XBPS)") from exc
    if result.returncode not in (0, 1, 255):
        raise CatalogError(f"Cannot compare XBPS versions {left!r} and {right!r}")
    return {0: 0, 1: 1, 255: -1}[result.returncode]


def literal_assignment(text, name, append=False):
    """Read a shell word without evaluating the template or its commands."""
    operator = r"\+=" if append else "="
    match = re.search(rf"^{re.escape(name)}{operator}", text, re.MULTILINE)
    if not match:
        return None
    lexer = shlex.shlex(text[match.end():], posix=True)
    lexer.whitespace_split = True
    try:
        return lexer.get_token()
    except ValueError as exc:
        raise CatalogError(f"Invalid quoted value for {name}") from exc


def read_templates(directory):
    templates = {}
    fields = ("pkgname", "version", "revision", "_commit", "short_desc", "homepage", "license", "distfiles")
    for path in sorted(Path(directory).glob("*/template")):
        text = path.read_text()
        values = {name: literal_assignment(text, name) for name in fields}
        name = path.parent.name
        if values["pkgname"] != name or not re.fullmatch(r"[A-Za-z0-9+._-]+", name):
            raise CatalogError(f"{path}: pkgname must match its directory")
        version = values["version"] or ""
        revision = values["revision"] or ""
        if not re.fullmatch(r"[0-9][A-Za-z0-9.+_~]*", version) or not revision.isdigit():
            raise CatalogError(f"{path}: version and revision must be literal XBPS values")
        commit = values["_commit"]
        if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise CatalogError(f"{path}: _commit must be a full Git commit hash")
        repo = re.search(r"https://github\.com/([\w.-]+/[\w.-]+)/", values["distfiles"] or "")
        packages = {name: values["short_desc"]}
        for sub in re.finditer(r"^([A-Za-z0-9+._-]+)_package\(\)\s*\{(.*?)^\}", text, re.MULTILINE | re.DOTALL):
            body = "\n".join(line.lstrip() for line in sub[2].splitlines())
            suffix = literal_assignment(body, "short_desc", append=True)
            packages[sub[1]] = literal_assignment(body, "short_desc") or ((values["short_desc"] or name) + (suffix or ""))
        templates[name] = {
            "name": name,
            "version": version,
            "revision": int(revision),
            "package_version": f"{version}_{revision}",
            "commit": commit,
            "repository": repo[1].removesuffix(".git") if repo else None,
            "description": values["short_desc"],
            "homepage": values["homepage"],
            "license": values["license"],
            "packages": packages,
        }
    if not templates:
        raise CatalogError(f"No package templates found in {directory}")
    return templates


class GitHub:
    def get(self, endpoint):
        """Use gh authentication when available, otherwise the standard HTTP client."""
        try:
            if shutil.which("gh"):
                response = subprocess.run(
                    ["gh", "api", endpoint], capture_output=True, text=True, timeout=35
                )
                if response.returncode:
                    status = re.search(r"HTTP (\d{3})", response.stderr)
                    code = int(status[1]) if status else None
                    raise GitHubError(f"GitHub lookup failed for {endpoint}" + (f" (HTTP {code})" if code else ""), code)
                return json.loads(response.stdout)
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "hinnom-package-catalog"}
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            with urlopen(Request(f"https://api.github.com/{endpoint}", headers=headers), timeout=35) as response:
                return json.load(response)
        except HTTPError as exc:
            raise GitHubError(f"GitHub lookup failed for {endpoint} (HTTP {exc.code})", exc.code) from exc
        except (URLError, TimeoutError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            raise GitHubError(f"GitHub lookup failed for {endpoint}: {exc}") from exc


def release_version(tag):
    value = re.sub(r"^[vV](?=\d)", "", tag)
    value = value.replace("-", ".")
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+_~]*", value):
        raise CatalogError(f"Cannot interpret upstream tag {tag!r} as an XBPS version")
    return value


def latest_upstream(template, api):
    repo = template["repository"]
    if template["commit"]:
        commit = api.get(f"repos/{repo}/commits/HEAD")
        sha = commit.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise CatalogError("Upstream returned an invalid commit hash")
        latest = {
            "kind": "commit", "version": None, "ref": sha,
            "url": f"https://github.com/{repo}/commit/{sha}",
            "published_at": commit.get("commit", {}).get("committer", {}).get("date"),
        }
        if sha == template["commit"]:
            return latest, "current"
        comparison = api.get(f"repos/{repo}/compare/{template['commit']}...{sha}")
        status = {"ahead": "outdated", "behind": "ahead", "identical": "current"}.get(comparison.get("status"))
        if not status:
            raise CatalogError("Pinned commit and upstream HEAD have diverged")
        return latest, status
    try:
        release = api.get(f"repos/{repo}/releases/latest")
    except GitHubError as exc:
        if exc.status != 404:
            raise
        release = None
    if release is not None:
        if release.get("draft") or release.get("prerelease") or not isinstance(release.get("tag_name"), str):
            raise CatalogError("Upstream did not return a stable release")
        tag = release["tag_name"]
        latest = {
            "kind": "release", "version": release_version(tag), "ref": tag,
            "url": f"https://github.com/{repo}/releases/tag/{quote(tag, safe='')}",
            "published_at": release.get("published_at"),
        }
    else:
        # Tag ordering is not a version ordering. Consider every stable version tag.
        best = None
        page = 1
        while True:
            tags = api.get(f"repos/{repo}/tags?per_page=100&page={page}")
            if not isinstance(tags, list):
                raise CatalogError("Upstream did not return a tag list")
            for entry in tags:
                tag = entry.get("name", "")
                if not re.fullmatch(r"[vV]?\d+(?:[.+_]\d+)*(?:p\d+)?", tag):
                    continue
                version = release_version(tag)
                if best is None or compare_versions(version, best[0]) > 0:
                    best = (version, tag)
            if len(tags) < 100:
                break
            page += 1
        if best is None:
            raise CatalogError("Upstream has no stable release or version tag")
        latest = {
            "kind": "tag", "version": best[0], "ref": best[1],
            "url": f"https://github.com/{repo}/tree/{quote(best[1], safe='')}",
            "published_at": None,
        }
    ordering = compare_versions(latest["version"], template["version"])
    return latest, {1: "outdated", 0: "current", -1: "ahead"}[ordering]


def previous_updates(document):
    """Accept a previous checker report or full catalog for last-known results."""
    if not document:
        return {}
    results = {}
    for package in document_packages(document):
        result = package.get("upstream") if "upstream" in package else package
        if result:
            validate_update(result)
            results[package.get("source_package", package["name"])] = result
    return results


def document_packages(document):
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise CatalogError("Unsupported catalog or update report schema")
    packages = document.get("packages")
    if not isinstance(packages, list):
        raise CatalogError("Catalog or update report must contain a packages array")
    names = set()
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise CatalogError("Each catalog or update entry must have a package name")
        if package["name"] in names:
            raise CatalogError(f"Duplicate catalog or update entry for {package['name']}")
        names.add(package["name"])
    return packages


def validate_update(update):
    if not isinstance(update, dict) or update.get("status") not in ("current", "outdated", "ahead", "unknown", "skipped"):
        raise CatalogError("Invalid upstream update status")
    latest = update.get("latest")
    if latest is None:
        if update["status"] in ("current", "outdated", "ahead"):
            raise CatalogError("Successful update results must contain upstream metadata")
        return
    if not isinstance(latest, dict) or latest.get("kind") not in ("release", "tag", "commit"):
        raise CatalogError("Invalid upstream metadata")
    if not isinstance(latest.get("ref"), str) or not latest["ref"]:
        raise CatalogError("Upstream metadata must identify a release, tag, or commit")
    if latest["kind"] != "commit" and not isinstance(latest.get("version"), str):
        raise CatalogError("Upstream release metadata must contain a version")


def check_updates(templates, api, previous=None):
    saved = previous_updates(previous)
    results = []
    for name, template in templates.items():
        result = {
            "name": name, "version": template["version"], "revision": template["revision"],
            "commit": template["commit"], "repository": template["repository"],
            "checked_at": timestamp(), "last_success_at": None,
            "status": "skipped", "latest": None, "stale": False, "error": None,
        }
        if template["repository"]:
            try:
                result["latest"], result["status"] = latest_upstream(template, api)
                result["last_success_at"] = result["checked_at"]
            except (CatalogError, KeyError, TypeError, AttributeError) as exc:
                result["status"] = "unknown"
                result["error"] = str(exc)
                old = saved.get(name, {})
                if old.get("repository") == template["repository"] and old.get("latest") and old.get("last_success_at"):
                    kind = "commit" if template["commit"] else "version"
                    old_kind = "commit" if old["latest"]["kind"] == "commit" else "version"
                    if kind == old_kind:
                        result["latest"] = old["latest"]
                        result["last_success_at"] = old["last_success_at"]
                        result["stale"] = True
        results.append(result)
    return {"schema_version": SCHEMA_VERSION, "generated_at": timestamp(), "packages": results}


def read_index(location):
    try:
        if urlparse(location).scheme in ("http", "https"):
            with urlopen(Request(location, headers={"User-Agent": "hinnom-package-catalog"}), timeout=35) as response:
                data = response.read()
        else:
            data = Path(location).read_bytes()
        # Read only the plist; never extract a remote archive onto the filesystem.
        with tarfile.open(fileobj=BytesIO(data), mode="r:*") as archive:
            member = archive.extractfile("index.plist")
            if member is None:
                raise CatalogError("index.plist is not a file")
            index = plistlib.load(member)
        if not isinstance(index, dict):
            raise CatalogError("index.plist must contain a package dictionary")
        if any(not isinstance(name, str) or not isinstance(props, dict) for name, props in index.items()):
            raise CatalogError("Repository index contains invalid package entries")
        return index
    except (OSError, URLError, TimeoutError, tarfile.TarError, ValueError, KeyError) as exc:
        raise CatalogError(f"Cannot read repository index {location}: {exc}") from exc


def read_repositories(specifications, base_url):
    repositories = []
    indexes = {}
    for specification in specifications or [f"x86_64={base_url}/x86_64"]:
        arch, separator, location = specification.partition("=")
        if not separator or not location or not re.fullmatch(r"[A-Za-z0-9_-]+", arch) or arch in indexes:
            raise CatalogError("Repositories must be unique ARCH=URL_OR_PATH values")
        remote = urlparse(location).scheme in ("http", "https")
        if remote:
            index_location = location if location.endswith("-repodata") else f"{location.rstrip('/')}/{arch}-repodata"
            url = index_location.rsplit("/", 1)[0]
        else:
            path = Path(location)
            index_location = str(path / f"{arch}-repodata") if path.is_dir() else str(path)
            url = f"{base_url.rstrip('/')}/{arch}"
        indexes[arch] = read_index(index_location)
        repositories.append({"architecture": arch, "url": url, "checked_at": timestamp()})
    return repositories, indexes


def build_status(template, upstream, version, revision):
    if template is None:
        return "unmanaged"
    ordering = compare_versions(template["package_version"], f"{version}_{revision}")
    if ordering > 0:
        return "awaiting_build"
    if upstream["status"] in ("unknown", "skipped"):
        return "unknown"
    latest = upstream["latest"]
    if latest["kind"] != "commit":
        return "outdated" if compare_versions(latest["version"], version) > 0 else "current"
    if ordering < 0:
        # The index does not identify the upstream commit of a newer snapshot.
        return "unknown"
    return "outdated" if upstream["status"] == "outdated" else "current"


def generate_catalog(templates, repositories, indexes, updates, source_url=SOURCE_URL):
    entries = document_packages(updates)
    for entry in entries:
        validate_update(entry)
    upstreams = {entry["name"]: entry for entry in entries}
    for name, template in templates.items():
        update = upstreams.get(name, {})
        if any(update.get(key) != template[key] for key in ("version", "revision", "commit", "repository")):
            raise CatalogError(f"Update report does not match the current template for {name}")
    owners = {}
    for name, template in templates.items():
        for package in template["packages"]:
            if package in owners:
                raise CatalogError(f"Duplicate package template for {package}")
            owners[package] = name
    names = set(owners)
    for index in indexes.values():
        names.update(index)
    packages = []
    for name in sorted(names):
        source = owners.get(name)
        template = templates.get(source)
        upstream = upstreams.get(source)
        package = {
            "name": name, "source_package": source,
            "description": template["packages"][name] if template else None,
            "homepage": template["homepage"] if template else None,
            "license": template["license"] if template else None,
            "template_url": f"{source_url.rstrip('/')}/blob/master/srcpkgs/{quote(source)}/template" if template else None,
            "template": {key: template[key] for key in ("version", "revision", "package_version", "commit")} if template else None,
            "upstream": upstream, "available_architectures": [], "builds": [],
        }
        for repository in repositories:
            arch = repository["architecture"]
            props = indexes[arch].get(name)
            if props is None:
                continue
            if not isinstance(props, dict) or not isinstance(props.get("pkgver"), str):
                raise CatalogError(f"Invalid repository metadata for {name} in {arch}")
            pkgver = props.get("pkgver", "")
            prefix = f"{name}-"
            version, _, revision = pkgver.removeprefix(prefix).rpartition("_")
            artifact_arch = props.get("architecture")
            if not pkgver.startswith(prefix) or not version or not revision.isdigit() or artifact_arch not in (arch, "noarch"):
                raise CatalogError(f"Invalid repository metadata for {name} in {arch}")
            package["available_architectures"].append(arch)
            for field, prop in (("description", "short_desc"), ("homepage", "homepage"), ("license", "license")):
                if props.get(prop):
                    package[field] = props[prop]
            if source is None:
                package["source_package"] = props.get("sourcepkg")
            package["builds"].append({
                "repository_architecture": arch, "architecture": artifact_arch,
                "version": version, "revision": int(revision), "package_version": f"{version}_{revision}",
                "pkgver": pkgver,
                "status": build_status(template, upstream, version, revision),
                "download_url": f"{repository['url']}/{quote(pkgver)}.{quote(artifact_arch)}.xbps",
                "download_size": props.get("filename-size"), "installed_size": props.get("installed_size"),
                "sha256": props.get("filename-sha256"), "dependencies": props.get("run_depends", []),
            })
        if not package["builds"]:
            package["status"] = "unpublished"
        else:
            states = {build["status"] for build in package["builds"]}
            package["status"] = next(state for state in ("awaiting_build", "unknown", "outdated", "unmanaged", "current") if state in states)
        packages.append(package)
    return {
        "schema_version": SCHEMA_VERSION, "generated_at": timestamp(), "source_url": source_url,
        "repositories": repositories, "packages": packages,
    }


def load_json(path):
    if path is None:
        return None
    try:
        document = json.loads(Path(path).read_text())
        document_packages(document)
        return document
    except (OSError, ValueError) as exc:
        raise CatalogError(f"Cannot read JSON from {path}: {exc}") from exc


def write_json(document, path=None):
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path is None:
        sys.stdout.write(text)
        return
    destination = Path(path)
    # Preserve the existing catalog if generation or writing fails.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
        temporary.replace(destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def check_main(argv=None):
    parser = argparse.ArgumentParser(description="Check upstream package releases and pinned commits.")
    parser.add_argument("--json", action="store_true", help="write a structured report instead of the human-readable table")
    parser.add_argument("--templates", type=Path, default=ROOT / "srcpkgs")
    parser.add_argument("--previous", type=Path, help="previous update report or catalog, to retain last successful results")
    args = parser.parse_args(argv)
    try:
        report = check_updates(read_templates(args.templates), GitHub(), load_json(args.previous))
        if args.json:
            write_json(report)
        else:
            for package in report["packages"]:
                current = package["commit"][:7] if package["commit"] else package["version"]
                status = package["status"]
                if status == "outdated":
                    latest = package["latest"]
                    label = "-> " + (latest["ref"][:7] if latest["kind"] == "commit" else latest["version"])
                else:
                    label = {"current": "up to date", "ahead": "ahead of upstream", "unknown": "lookup failed", "skipped": "skipped (not on GitHub)"}[status]
                print(f"{package['name']:<30} {current:<12} {label}")
                if package["error"]:
                    print(f"  {package['error']}", file=sys.stderr)
        states = {package["status"] for package in report["packages"]}
        return 2 if "unknown" in states else 1 if "outdated" in states else 0
    except (CatalogError, OSError) as exc:
        print(f"check-updates: {exc}", file=sys.stderr)
        return 2


def catalog_main(argv=None):
    parser = argparse.ArgumentParser(description="Generate the package catalog from published XBPS indexes and current templates.")
    parser.add_argument("--templates", type=Path, default=ROOT / "srcpkgs")
    parser.add_argument("--repository", action="append", metavar="ARCH=URL_OR_PATH", help="repeat for each published repository; defaults to the live x86_64 repository")
    parser.add_argument("--base-url", default=REPOSITORY_URL, help="download base URL when reading a local index")
    parser.add_argument("--source-url", default=SOURCE_URL, help="source repository URL for template links")
    parser.add_argument("--updates", type=Path, help="reuse a check-updates --json report instead of checking upstreams")
    parser.add_argument("--previous", type=Path, help="previous catalog or update report for last successful upstream results")
    parser.add_argument("--output", type=Path, help="write atomically to this file; default: stdout")
    args = parser.parse_args(argv)
    try:
        templates = read_templates(args.templates)
        repositories, indexes = read_repositories(args.repository, args.base_url)
        previous = args.previous or (args.output if args.output and args.output.exists() else None)
        updates = load_json(args.updates) if args.updates else check_updates(templates, GitHub(), load_json(previous))
        catalog = generate_catalog(templates, repositories, indexes, updates, args.source_url)
        write_json(catalog, args.output)
        return 2 if any(package["status"] == "unknown" for package in updates["packages"]) else 0
    except (CatalogError, OSError) as exc:
        print(f"generate-catalog: {exc}", file=sys.stderr)
        return 2
