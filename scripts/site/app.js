(() => {
  "use strict";

  const form = document.querySelector("#filters");
  const search = document.querySelector("#search");
  const updates = document.querySelector("#updates-only");
  const architecture = document.querySelector("#architecture");
  const count = document.querySelector("#result-count");
  const empty = document.querySelector("#empty-state");
  const normalize = (value) => value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const packages = Array.from(document.querySelectorAll(".package")).map((element) => ({
    element,
    text: normalize(element.dataset.search),
    update: element.dataset.update === "true",
    architectures: element.dataset.architectures.split(" "),
  }));

  function writeState() {
    const url = new URL(window.location.href);
    for (const [name, value] of [["q", search.value], ["updates", updates.checked ? "1" : ""], ["arch", architecture?.value || ""]]) {
      if (value) url.searchParams.set(name, value);
      else url.searchParams.delete(name);
    }
    try {
      const target = document.getElementById(decodeURIComponent(url.hash.slice(1)));
      if (target?.classList.contains("package") && target.hidden) url.hash = "";
    } catch { /* Ignore malformed fragments. */ }
    try {
      window.history.replaceState(null, "", url);
    } catch {
      // Filtering also works when the generated HTML is opened directly from disk.
    }
  }

  function filter(write = true) {
    const terms = normalize(search.value.trim()).split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const item of packages) {
      const matches = terms.every((term) => item.text.includes(term))
        && (!updates.checked || item.update)
        && (!architecture?.value || item.architectures.includes(architecture.value));
      item.element.hidden = !matches;
      if (matches) visible += 1;
    }
    count.textContent = visible === packages.length
      ? `${visible} ${visible === 1 ? "package" : "packages"}`
      : `${visible} of ${packages.length} packages`;
    empty.hidden = visible !== 0;
    const noUpdates = updates.checked && !terms.length && !architecture?.value;
    empty.querySelector("h2").textContent = noUpdates ? "No known updates" : "No matching packages";
    empty.querySelector("p").textContent = noUpdates
      ? "No packages in this catalog have a known update."
      : "Try another search or clear the filters.";
    if (write) writeState();
  }

  function readState() {
    const parameters = new URL(window.location.href).searchParams;
    search.value = parameters.get("q") || "";
    updates.checked = parameters.get("updates") === "1";
    if (architecture) {
      const value = parameters.get("arch") || "";
      architecture.value = Array.from(architecture.options).some((option) => option.value === value) ? value : "";
    }
    filter(false);
  }

  function clearFilters() {
    search.value = "";
    updates.checked = false;
    if (architecture) architecture.value = "";
    filter();
  }

  function openPackage() {
    let target;
    try {
      target = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    } catch {
      return;
    }
    if (!target?.classList.contains("package")) return;
    if (target.hidden) clearFilters();
    target.open = true;
    target.scrollIntoView({ block: "start" });
  }

  form.addEventListener("submit", (event) => event.preventDefault());
  search.addEventListener("input", () => filter());
  updates.addEventListener("change", () => filter());
  architecture?.addEventListener("change", () => filter());
  document.querySelector("#clear-filters").addEventListener("click", () => {
    clearFilters();
    search.focus();
  });
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const editing = target instanceof Element && (target.closest("input, textarea, select") || target.isContentEditable);
    if (event.key === "/" && !editing && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      search.focus();
    } else if (event.key === "Escape" && document.activeElement === search) {
      search.value = "";
      filter();
    }
  });
  window.addEventListener("popstate", () => { readState(); openPackage(); });
  window.addEventListener("hashchange", openPackage);

  async function copy(text) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch { /* Fall back for browsers without clipboard permission. */ }
    }
    const previousFocus = document.activeElement;
    const area = document.createElement("textarea");
    area.value = text;
    area.className = "sr-only";
    area.setAttribute("aria-label", "Command to copy");
    document.body.append(area);
    area.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch { /* Report failure below. */ }
    area.remove();
    previousFocus?.focus({ preventScroll: true });
    return copied;
  }

  document.addEventListener("click", async (event) => {
    const button = event.target instanceof Element && event.target.closest("button[data-copy]");
    if (!button) return;
    button.disabled = true;
    const label = button.textContent;
    const copied = await copy(button.dataset.copy);
    button.textContent = copied ? "Copied" : "Copy failed";
    document.querySelector("#copy-status").textContent = copied
      ? "Command copied to clipboard."
      : "Could not copy. Select and copy the command manually.";
    window.setTimeout(() => { button.textContent = label; button.disabled = false; }, 1800);
  });

  function refreshTimes() {
    const now = Date.now();
    for (const time of document.querySelectorAll("time[data-relative]")) {
      const value = Date.parse(time.dateTime);
      if (!Number.isFinite(value)) continue;
      const minutes = Math.max(0, Math.floor((now - value) / 60000));
      time.textContent = minutes < 1 ? "just now"
        : minutes < 60 ? `${minutes}m ago`
        : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago`
        : `${Math.floor(minutes / 1440)}d ago`;
    }
    const checked = document.querySelector(".checked time");
    const stale = checked && now - Date.parse(checked.dateTime) > 12 * 60 * 60 * 1000;
    const note = document.querySelector("#freshness-note");
    note.hidden = !stale;
    note.textContent = stale ? "Update data is over 12 hours old. Package statuses reflect the last check." : "";
  }

  readState();
  form.hidden = false;
  for (const button of document.querySelectorAll("button[data-copy]")) button.hidden = false;
  openPackage();
  refreshTimes();
  window.setInterval(refreshTimes, 60000);
})();
