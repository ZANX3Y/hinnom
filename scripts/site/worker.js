// Starts scheduled GitHub workflows. GitHub's own schedule events are often
// delayed by hours or skipped, so Cloudflare Cron Triggers dispatch them instead.
// Static assets are served before this script runs.

const REPOSITORY = "ZANX3Y/hinnom";

// Keys must match `triggers.crons` in wrangler.toml.
const SCHEDULES = {
  "0 * * * *": ["website.yml"],
  "0 5 * * *": ["compat.yml"],
};

async function dispatch(workflow, token) {
  const response = await fetch(
    `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "User-Agent": "hinnom-cron",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "master" }),
    },
  );
  if (!response.ok) {
    throw new Error(`Dispatching ${workflow} failed: HTTP ${response.status} ${await response.text()}`);
  }
}

export default {
  async scheduled(controller, env) {
    const workflows = SCHEDULES[controller.cron];
    if (!workflows) throw new Error(`No workflows for cron ${controller.cron}`);
    if (!env.GITHUB_TOKEN) throw new Error("The GITHUB_TOKEN secret is not set");
    const results = await Promise.allSettled(workflows.map((workflow) => dispatch(workflow, env.GITHUB_TOKEN)));
    const errors = results.filter((result) => result.status === "rejected").map((result) => result.reason);
    if (errors.length) throw new AggregateError(errors, errors.map((error) => error.message).join("; "));
  },
};
