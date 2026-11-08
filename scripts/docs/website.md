# Website

A static package browser with search, filters, and expandable build details.

## Build and preview

Requires the [catalog dependencies](package-catalog.md#requirements) and Jinja2.
Install `python3-Jinja2` on Void, or use
`python3 -m pip install -r scripts/site/requirements.txt` in a virtual environment.

Run from the repository root:

```sh
scripts/generate-catalog --output packages.json
scripts/build-site
python3 -m http.server 8765 --bind 127.0.0.1 --directory dist
```

Open <http://127.0.0.1:8765>. Generated files go into `dist/`.

Templates, assets, and Wrangler configuration live in `scripts/site/`; rendering
is handled by `scripts/package_site.py`. Use `scripts/build-site --help` for
alternate catalog and output paths.

## Deployment

The [Publish website workflow](../../.github/workflows/website.yml) tests, builds,
and deploys on `master` every six hours, on relevant pushes, after successful
package builds, or through **Run workflow**.

Add these repository Actions secrets:

| Secret | Value |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | A token with `Workers Scripts Write` for that account |

The first deployment creates the `hinnom` Worker. Attach a
[custom domain](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/).

The workflow caches upstream results between runs. Failed upstream checks can
still be published as unknown; failed tests, invalid catalogs, and build errors
stop deployment.

For a manual deployment, build the site, authenticate Wrangler, then run:

```sh
npx wrangler deploy --config scripts/site/wrangler.toml
```

## Tests

```sh
python3 -B -m unittest discover -s scripts/tests -v
```
