# Package catalog

The metadata tools combine published XBPS packages, local templates, and upstream
versions into the JSON used by the [website](website.md).

## Requirements

- Python 3.14 or newer.
- `xbps-uhelper` from XBPS.
- Optional GitHub authentication through `gh`, `GH_TOKEN`, or `GITHUB_TOKEN`.

## Usage

Run commands from the repository root:

```sh
scripts/check-updates
scripts/check-updates --json > /tmp/hinnom-updates.json
scripts/generate-catalog --output packages.json
```

The generator reads the live x86_64 repository by default. Repeat
`--repository ARCH=URL_OR_PATH` to select other repositories or local indexes.
Use `--updates FILE` to reuse a matching update report without checking upstreams.
Run either script with `--help` for all options.

An existing output catalog supplies last-known upstream results when a check
fails. Use `--previous FILE` to select a different previous catalog or report.
Cached results are marked stale.

## Statuses

Each package includes metadata, upstream results, and published builds with
versions, architectures, downloads, dependencies, and checksums.

| Status | Meaning |
| --- | --- |
| `current` | No newer upstream version was found |
| `outdated` | A newer upstream version is available |
| `awaiting_build` | The template is newer than the published package |
| `unpublished` | No package is published in the selected repositories |
| `unknown` | The upstream state could not be determined |
| `unmanaged` | A published package no longer has a template |

## Exit codes

`check-updates` returns `0` when current, `1` when updates are available, and `2`
on failure.

`generate-catalog` returns `0` on success or `2` on failure. Upstream lookup
failures still produce a catalog with unknown statuses. Index or input failures
leave the existing output untouched.
