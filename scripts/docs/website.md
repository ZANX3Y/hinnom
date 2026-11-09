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

The [Publish website workflow](../../.github/workflows/website.yml) checks hourly
and refreshes catalogs that are at least six hours old. GitHub may delay or skip
scheduled events; hourly checks give a missed refresh another chance. Relevant
pushes to `master`, successful package builds, and **Run workflow** refresh
immediately.

Create the `hinnom` Worker in Cloudflare first, then add these repository Actions
secrets:

| Secret | Value |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | A token with Workers Editor access to `hinnom` |

For a token scoped to **Specified Workers**, select `hinnom` after creating it.
Creating Workers through the API requires product-level Workers Admin access.
See [Workers permissions](https://developers.cloudflare.com/workers/authorization/workers/).

Attach a
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
