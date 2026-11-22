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

The build also writes `robots.txt` and `sitemap.xml`, and the page links to its
canonical URL. These use `https://hinnom.znx.pw/` unless `--site-url` says
otherwise. `robots.txt` keeps crawlers out of the repository paths, which
redirect to package downloads.

## Deployment

The [Publish website workflow](../../.github/workflows/website.yml) rebuilds the
site on relevant pushes to `master`, after successful package builds, and on
**Run workflow**.

Scheduled runs come from Cloudflare instead of GitHub, whose schedule events are
often delayed by hours or skipped. Cron Triggers on the `hinnom` Worker
([`worker.js`](../site/worker.js)) dispatch the website workflow hourly and the
[Void compatibility check](../../.github/workflows/compat.yml) daily at 05:00 UTC.
Schedules are set in `triggers.crons` in `wrangler.toml` and mapped to workflows
in `worker.js`; both lists must match. Failed dispatches appear under the
Worker's **Cron Events** and logs in the Cloudflare dashboard.

Create the `hinnom` Worker in Cloudflare first, then add these repository Actions
secrets:

| Secret | Value |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | A token with Workers Editor access to `hinnom` |

For a token scoped to **Specified Workers**, select `hinnom` after creating it.
Creating Workers through the API requires product-level Workers Admin access.
See [Workers permissions](https://developers.cloudflare.com/workers/authorization/workers/).

The Worker needs a GitHub token to dispatch workflows. Create a fine-grained
personal access token for this repository only, with **Actions: Read and write**
permission, and store it as a Worker secret:

```sh
npx wrangler secret put GITHUB_TOKEN --config scripts/site/wrangler.toml
```

Secrets persist across deployments. Renew the token before it expires; scheduled
runs stop until it is replaced.

Attach a
[custom domain](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/).
The `*.workers.dev` address is disabled so the site has a single URL for search
engines. Also enable **Always Use HTTPS** for the zone (**SSL/TLS** → **Edge
Certificates**), so `http://` requests redirect instead of serving a second copy.

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
