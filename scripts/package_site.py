"""Render the package catalog as a small, progressively enhanced static site."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError, select_autoescape

from package_catalog import CatalogError, ROOT, document_packages, load_json, write_json


SITE = Path(__file__).resolve().parent / "site"
STATUSES = {
    "current": ("Current", "No newer upstream version at the last check"),
    "outdated": ("Update available", "A newer upstream version is available"),
    "awaiting_build": ("Awaiting build", "The template is newer than the published package"),
    "unpublished": ("Not built", "No package is published in the selected repositories"),
    "unknown": ("Unknown", "The upstream state could not be determined"),
    "unmanaged": ("Unmaintained", "This published package no longer has a template"),
}


def display(value):
    return str(value) if value is not None else "—"


def safe_url(url):
    parsed = urlparse(url or "")
    if url and (url.startswith("#") or (parsed.scheme in ("http", "https") and parsed.netloc)):
        return url
    return ""


def time_label(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise CatalogError(f"Invalid catalog timestamp: {value!r}") from None
    return date.strftime("%d %b %Y, %H:%M UTC").lstrip("0")


def size_label(value):
    if value is None:
        return "Size unknown"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def upstream_update(package):
    upstream = package.get("upstream") or {}
    latest = upstream.get("latest")
    if latest and upstream.get("stale"):
        value = latest["version"] if latest["kind"] != "commit" else latest["ref"][:12]
        return {"value": value, "note": "last known", "available": False}
    if package["status"] in ("outdated", "awaiting_build"):
        if latest and upstream["status"] == "outdated":
            value = latest["version"] if latest["kind"] != "commit" else latest["ref"][:12]
            note = "commit" if latest["kind"] == "commit" else ""
        elif package.get("template"):
            value = package["template"]["package_version"]
            note = "in template"
        else:
            value, note = "—", ""
        return {"value": value, "note": note, "available": True}
    return None


def package_context(package):
    name = package["name"]
    if not re.fullmatch(r"[A-Za-z0-9+._-]+", name):
        raise CatalogError(f"Invalid package name: {name!r}")
    return {
        **package,
        "versions": list(dict.fromkeys(build["package_version"] for build in package["builds"])),
        "has_update": package["status"] in ("outdated", "awaiting_build"),
        "upstream_update": upstream_update(package),
        "search_text": " ".join(str(package.get(key) or "") for key in ("name", "description", "source_package")),
    }


def render_site(catalog):
    packages = document_packages(catalog)
    repositories = catalog.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise CatalogError("The site requires a full catalog with repository information")
    architectures = [repository["architecture"] for repository in repositories]
    installation = []
    for repository in repositories:
        if urlparse(repository["url"]).scheme not in ("https", "http") or not re.fullmatch(r"[A-Za-z0-9_-]+", repository["architecture"]):
            raise CatalogError("Invalid repository URL or architecture")
        if not re.fullmatch(r"https?://[A-Za-z0-9._~:/%+-]+", repository["url"]):
            raise CatalogError("Repository URL cannot be used in an install command")
        filename = "00-hinnom.conf" if len(repositories) == 1 else f"00-hinnom-{repository['architecture']}.conf"
        value = f"echo 'repository={repository['url']}' | sudo tee /etc/xbps.d/{filename}"
        installation.append({"architecture": repository["architecture"], "command": value})
    checks = [package["upstream"]["checked_at"] for package in packages if package.get("upstream") and package["upstream"].get("checked_at")]
    checked = min(checks) if checks else catalog["generated_at"]
    environment = Environment(
        loader=FileSystemLoader(SITE / "templates"),
        autoescape=select_autoescape(("html",)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    environment.filters.update(display=display, safe_url=safe_url, time_label=time_label, size_label=size_label)
    environment.globals["statuses"] = STATUSES
    context = {
        "source_url": catalog.get("source_url"),
        "package_count": len(packages),
        "installation": installation,
        "architectures": architectures,
        "update_count": sum(package["status"] in ("outdated", "awaiting_build") for package in packages),
        "checked_at": checked,
        "packages": [package_context(package) for package in packages],
        "known_names": {package["name"] for package in packages},
        "source_count": len({package["source_package"] for package in packages if package.get("source_package")}),
    }
    return environment.get_template("index.html").render(context)


def build_site(catalog, output):
    output = Path(output)
    if output.resolve().is_relative_to(SITE.resolve()):
        raise CatalogError("Build output cannot overwrite the site templates")
    html = render_site(catalog)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("styles.css", "app.js", "_headers", "hinnom.svg", "hinnom-icon.svg"):
        shutil.copyfile(SITE / name, output / name)
    (output / "index.html").write_text(html)
    write_json(catalog, output / "packages.json")
    return len(catalog["packages"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the static package-list site from a generated catalog.")
    parser.add_argument("--catalog", type=Path, default=ROOT / "packages.json")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    try:
        count = build_site(load_json(args.catalog), args.output)
        print(f"Built {count} packages into {args.output}")
        return 0
    except (CatalogError, OSError, KeyError, TypeError, ValueError, TemplateError) as exc:
        print(f"build-site: {exc}", file=sys.stderr)
        return 2
