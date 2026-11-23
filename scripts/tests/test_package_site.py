from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_site as site
from package_catalog import CatalogError


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.content = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        self.elements.append((tag, dict(attributes)))

    def handle_data(self, value):
        self.content.append(value)

    def named(self, name):
        return [(tag, attributes) for tag, attributes in self.elements if attributes.get("id") == name]

    def packages(self):
        return [attributes for tag, attributes in self.elements if tag == "details" and attributes.get("class") == "package"]


def fixture():
    return {
        "schema_version": 1, "generated_at": "2026-11-07T00:00:00Z",
        "source_url": "https://github.com/ZANX3Y/hinnom",
        "repositories": [{"architecture": "x86_64", "url": "https://hinnom.znx.pw/x86_64"}],
        "packages": [{
            "name": "example", "description": "Example package", "source_package": "example",
            "homepage": "https://example.org", "license": "MIT", "status": "current",
            "template_url": "https://example.org/template", "available_architectures": ["x86_64"],
            "template": {"package_version": "1.0_1", "commit": None},
            "upstream": {
                "status": "current", "checked_at": "2026-11-07T00:00:00Z", "stale": False,
                "latest": {"kind": "release", "version": "1.0", "ref": "v1.0", "url": "https://example.org/release"},
            },
            "builds": [{
                "repository_architecture": "x86_64", "architecture": "x86_64", "package_version": "1.0_1",
                "status": "current", "download_url": "https://hinnom.znx.pw/x86_64/example-1.0_1.x86_64.xbps",
                "download_size": 1024, "dependencies": ["glibc>=2.41_1"], "sha256": "a" * 64,
            }],
        }],
    }


class SiteTests(unittest.TestCase):
    def test_package_information_is_present_without_a_javascript_fetch(self):
        document = Document(site.render_site(fixture()))
        content = " ".join(document.content)
        self.assertIn("Example package", content)
        self.assertIn("1.0_1", content)
        self.assertIn("MIT", content)
        self.assertIn("glibc>=2.41_1", content)
        self.assertEqual(len(document.packages()), 1)
        self.assertEqual(document.packages()[0]["data-architectures"], "x86_64")
        self.assertIn("hidden", document.named("filters")[0][1])
        self.assertFalse(document.named("architecture"))
        self.assertTrue(any(tag == "a" and attributes.get("href", "").endswith(".xbps") for tag, attributes in document.elements))

    def test_metadata_is_escaped_and_executable_urls_are_omitted(self):
        catalog = fixture()
        description = '</summary><script>alert("bad")</script>'
        catalog["packages"][0]["description"] = description
        catalog["packages"][0]["homepage"] = "javascript:alert(1)"
        html = site.render_site(catalog)
        self.assertNotIn('<script>alert("bad")</script>', html)
        self.assertIn("&lt;/summary&gt;&lt;script&gt;", html)
        document = Document(html)
        self.assertEqual(len(document.packages()), 1)
        self.assertEqual(document.packages()[0]["data-search"], f"example {description} example")
        self.assertFalse(any(attributes.get("href", "").startswith("javascript:") for _, attributes in document.elements))

    def test_release_update_and_pending_revision_are_visible(self):
        for status in ("outdated", "awaiting_build"):
            with self.subTest(status=status):
                catalog = fixture()
                package = catalog["packages"][0]
                package["status"] = status
                if status == "outdated":
                    package["upstream"]["status"] = "outdated"
                    package["upstream"]["latest"]["version"] = "1.1"
                    expected = "1.1"
                else:
                    package["template"]["package_version"] = "1.0_2"
                    expected = "1.0_2"
                document = Document(site.render_site(catalog))
                self.assertEqual(document.packages()[0]["data-update"], "true")
                self.assertIn(expected, " ".join(document.content))

    def test_current_packages_have_a_quiet_status_cell(self):
        html = site.render_site(fixture())
        start = html.index("<summary>", html.index('class="package"'))
        row = html[start:html.index("</summary>", start)]
        self.assertIn('<span class="status-cell quiet"><span class="sr-only">Current</span></span>', row)
        self.assertNotIn("status-current", row)

    def test_stale_result_is_labelled_as_last_known(self):
        catalog = fixture()
        package = catalog["packages"][0]
        package["status"] = "unknown"
        package["upstream"].update(status="unknown", stale=True, last_success_at="2026-11-06T00:00:00Z")
        document = Document(site.render_site(catalog))
        content = " ".join(document.content)
        self.assertIn("last known", content)
        self.assertIn("Upstream check failed.", content)
        self.assertIn("Last successful check:", content)
        self.assertEqual(document.packages()[0]["data-update"], "false")

    def test_snapshots_show_commit_references_for_updates(self):
        catalog = fixture()
        package = catalog["packages"][0]
        package["status"] = "outdated"
        package["template"]["commit"] = "a" * 40
        package["upstream"]["status"] = "outdated"
        package["upstream"]["latest"].update(kind="commit", version=None, ref="b" * 40)
        content = " ".join(Document(site.render_site(catalog)).content)
        self.assertIn("b" * 12, content)
        self.assertIn("Pinned commit", content)

    def test_only_snapshots_carry_the_git_badge(self):
        catalog = fixture()
        self.assertNotIn('class="snapshot"', site.render_site(catalog))
        catalog["packages"][0]["template"]["commit"] = "a" * 40
        html = site.render_site(catalog)
        self.assertEqual(html.count('class="snapshot"'), 1)
        self.assertIn("Built from upstream Git commit " + "a" * 12, html)

    def test_multiple_architectures_keep_their_versions_and_enable_filter(self):
        catalog = fixture()
        catalog["repositories"].append({"architecture": "aarch64", "url": "https://hinnom.znx.pw/aarch64"})
        package = catalog["packages"][0]
        package["available_architectures"].append("aarch64")
        second = deepcopy(package["builds"][0])
        second.update(repository_architecture="aarch64", architecture="noarch", package_version="0.9_1")
        package["builds"].append(second)
        document = Document(site.render_site(catalog))
        self.assertTrue(document.named("architecture"))
        self.assertEqual(document.packages()[0]["data-architectures"], "x86_64 aarch64")
        self.assertIn("0.9_1", " ".join(document.content))
        self.assertIn("architecture-independent", " ".join(document.content))
        self.assertIn("multi-arch", [attributes for tag, attributes in document.elements if tag == "section"][0]["class"])
        self.assertEqual(" ".join(document.content).count("1. Add the repository for your architecture"), 1)
        self.assertEqual(" ".join(document.content).count("/etc/xbps.d/00-hinnom.conf"), 2)

    def test_unpublished_package_has_no_install_or_download_command(self):
        catalog = fixture()
        package = catalog["packages"][0]
        package.update(builds=[], available_architectures=[], status="unpublished")
        html = site.render_site(catalog)
        self.assertNotIn("sudo xbps-install example", html)
        self.assertNotIn("Download .xbps", html)
        self.assertIn("No build has been published yet.", html)

    def test_source_links_target_actual_packages_and_ids_are_unique(self):
        catalog = fixture()
        subpackage = deepcopy(catalog["packages"][0])
        subpackage["name"] = "example-devel"
        catalog["packages"].append(subpackage)
        document = Document(site.render_site(catalog))
        self.assertTrue(any(attributes.get("href") == "#pkg-example" for _, attributes in document.elements))
        ids = [attributes["id"] for _, attributes in document.elements if "id" in attributes]
        self.assertEqual(len(ids), len(set(ids)))

    def test_empty_catalog_has_visible_empty_state_without_javascript(self):
        catalog = fixture()
        catalog["packages"] = []
        document = Document(site.render_site(catalog))
        self.assertNotIn("hidden", document.named("empty-state")[0][1])

    def test_page_links_to_its_canonical_url(self):
        document = Document(site.render_site(fixture(), "https://example.org/sub"))
        links = [attributes for tag, attributes in document.elements if tag == "link" and attributes.get("rel") == "canonical"]
        self.assertEqual(links, [{"rel": "canonical", "href": "https://example.org/sub/"}])
        with self.assertRaises(CatalogError):
            site.render_site(fixture(), "http://example.org")

    def test_robots_blocks_repository_paths_and_names_the_sitemap(self):
        catalog = fixture()
        catalog["repositories"].append({"architecture": "aarch64", "url": "https://mirror.example.org/aarch64"})
        self.assertEqual(
            site.robots_txt(catalog, "https://hinnom.znx.pw"),
            "User-agent: *\nDisallow: /x86_64/\n\nSitemap: https://hinnom.znx.pw/sitemap.xml\n",
        )

    def test_sitemap_lists_the_page_with_the_catalog_time(self):
        sitemap = site.sitemap_xml(fixture(), "https://hinnom.znx.pw")
        self.assertIn("<loc>https://hinnom.znx.pw/</loc><lastmod>2026-11-07T00:00:00Z</lastmod>", sitemap)
        self.assertIn("<loc>https://hinnom.znx.pw/packages/example/</loc>", sitemap)

    def test_build_output_contains_the_catalog_and_only_local_page_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = fixture()
            self.assertEqual(site.build_site(catalog, root), 1)
            self.assertEqual(json.loads((root / "packages.json").read_text()), catalog)
            for page in ("index.html", "packages/example/index.html"):
                document = Document((root / page).read_text())
                assets = [attributes["src"] for tag, attributes in document.elements if tag in ("script", "img")]
                assets += [attributes["href"] for tag, attributes in document.elements if tag == "link" and attributes.get("rel") != "canonical"]
                for asset in assets:
                    self.assertTrue(((root / page).parent / asset).is_file(), f"{page}: {asset}")
            self.assertTrue((root / "_headers").is_file())
            self.assertTrue((root / "robots.txt").is_file())
            self.assertTrue((root / "sitemap.xml").is_file())
            self.assertFalse((root / "templates").exists())
            self.assertFalse((root / "requirements.txt").exists())

    def test_each_package_has_a_page_with_its_own_metadata(self):
        pages = site.render_pages(fixture(), "https://hinnom.znx.pw")
        self.assertEqual(sorted(pages), ["index.html", "packages/example/index.html"])
        document = Document(pages["packages/example/index.html"])
        head = {attributes.get("name") or attributes.get("rel"): attributes for tag, attributes in document.elements if tag in ("meta", "link")}
        self.assertEqual(head["canonical"]["href"], "https://hinnom.znx.pw/packages/example/")
        self.assertEqual(head["description"]["content"], "Example package. Install example on Void Linux from the unofficial Hinnom repository.")
        content = " ".join(document.content)
        self.assertIn("example for Void Linux — Hinnom", content)
        self.assertIn("sudo xbps-install example", content)
        self.assertIn("sudo xbps-install -S", content)
        self.assertNotIn("Package page", content)

    def test_pages_have_open_graph_metadata_with_an_absolute_image(self):
        pages = site.render_pages(fixture(), "https://hinnom.znx.pw")
        document = Document(pages["packages/example/index.html"])
        og = {attributes["property"]: attributes["content"] for tag, attributes in document.elements if tag == "meta" and "property" in attributes}
        self.assertEqual(og["og:title"], "example for Void Linux — Hinnom")
        self.assertEqual(og["og:description"], "Example package. Install example on Void Linux from the unofficial Hinnom repository.")
        self.assertEqual(og["og:url"], "https://hinnom.znx.pw/packages/example/")
        self.assertEqual(og["og:image"], "https://hinnom.znx.pw/hinnom-og.png")
        index = Document(pages["index.html"])
        image = [attributes["content"] for tag, attributes in index.elements if tag == "meta" and attributes.get("property") == "og:image"]
        self.assertEqual(image, ["https://hinnom.znx.pw/hinnom-og.png"])

    def test_list_links_to_package_pages_and_pages_link_to_source_packages(self):
        catalog = fixture()
        subpackage = deepcopy(catalog["packages"][0])
        subpackage["name"] = "example-devel"
        catalog["packages"].append(subpackage)
        pages = site.render_pages(catalog)
        links = [attributes.get("href") for tag, attributes in Document(pages["index.html"]).elements if tag == "a"]
        self.assertIn("packages/example-devel/", links)
        links = [attributes.get("href") for tag, attributes in Document(pages["packages/example-devel/index.html"]).elements if tag == "a"]
        self.assertIn("../../packages/example/", links)
        self.assertIn("../../", links)

    def test_package_pages_name_architectures_and_only_their_repositories(self):
        catalog = fixture()
        catalog["repositories"].append({"architecture": "aarch64", "url": "https://hinnom.znx.pw/aarch64"})
        package = catalog["packages"][0]
        package["available_architectures"] = ["aarch64"]
        package["builds"][0]["repository_architecture"] = "aarch64"
        html = site.render_pages(catalog)["packages/example/index.html"]
        self.assertIn("repository=https://hinnom.znx.pw/aarch64", html)
        self.assertNotIn("repository=https://hinnom.znx.pw/x86_64", html)
        self.assertNotIn("for your architecture", html)

        second = deepcopy(package["builds"][0])
        second.update(repository_architecture="x86_64", package_version="0.9_1")
        package["builds"].append(second)
        package["available_architectures"].append("x86_64")
        document = Document(site.render_pages(catalog)["packages/example/index.html"])
        facts = [attributes for tag, attributes in document.elements if attributes.get("class") == "fact-version"]
        self.assertEqual(len(facts), 2)
        content = " ".join(document.content)
        self.assertIn("1. Add the Hinnom repository for your architecture", content)
        self.assertEqual(content.count("Add the Hinnom repository"), 1)

    def test_package_names_cannot_escape_the_packages_directory(self):
        for name in ("..", ".hidden", "a/b"):
            with self.subTest(name=name):
                catalog = fixture()
                catalog["packages"][0]["name"] = name
                with self.assertRaises(CatalogError):
                    site.render_pages(catalog)

    def test_rebuild_removes_pages_of_dropped_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = fixture()
            catalog["packages"][0]["name"] = "old"
            site.build_site(catalog, root)
            self.assertTrue((root / "packages/old/index.html").is_file())
            site.build_site(fixture(), root)
            self.assertFalse((root / "packages/old").exists())
            self.assertTrue((root / "packages/example/index.html").is_file())

    def test_build_output_cannot_overwrite_template_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            templates = root / "templates"
            templates.mkdir()
            template = templates / "index.html"
            template.write_text("source template")
            with patch.object(site, "SITE", root):
                for output in (root, templates, templates / "nested"):
                    with self.subTest(output=output):
                        with self.assertRaisesRegex(CatalogError, "cannot overwrite"):
                            site.build_site(fixture(), output)
            self.assertEqual(template.read_text(), "source template")

    def test_invalid_catalog_leaves_previous_page_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("previous page")
            with self.assertRaises(CatalogError):
                site.build_site({"schema_version": 1, "packages": []}, root)
            self.assertEqual((root / "index.html").read_text(), "previous page")


if __name__ == "__main__":
    unittest.main()
