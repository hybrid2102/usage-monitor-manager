/* Usage Monitor Manager - window front-end.
 *
 * Vanilla JS, no framework and no build step (PLAN.md section 4.2). Everything
 * comes from the Python side through window.pywebview.api (the JsApi class in
 * ui/window.py); progress arrives as events pushed into window.umm.onEvent.
 *
 * The markdown renderer at the bottom is deliberately tiny: headings, lists,
 * links, inline/fenced code and bold are what the monitors' release notes use.
 */

"use strict";

const state = {
  page: "dashboard",
  rows: [],
  updates: { available: [], plans: [], last_check: null, error: null },
  selected: null,
  busy: false,
  watchdog: { count: 0, events: [] },
  archived: [],
  manager: null,
  kinds: [],
  profiles: [],
  diag: { session: null, source: "", timer: null },
  view: "cards",
};

/* Filled from the catalog once the bridge answers (see loadTranslations). */
const WATCHDOG_LABELS = {
  crashed: "js.watchdog_crashed",
  duplicate: "js.watchdog_duplicate",
  orphan: "js.watchdog_orphan",
};

/* --------------------------------------------------------------- i18n */

/* The whole catalog arrives in one call (JsApi.get_translations) and stays
 * here: a round trip per label over the pywebview bridge would be absurd. The
 * Italian text is also in the HTML, so the page is readable before the bridge
 * answers and a key with no translation simply keeps what the markup says. */
let STRINGS = {};

function t(key, values) {
  let text = STRINGS[key];
  if (text === undefined) return key;
  if (values) {
    for (const [name, value] of Object.entries(values)) {
      text = text.split("{" + name + "}").join(String(value));
    }
  }
  return text;
}

function applyTranslations(root) {
  const scope = root || document;
  for (const el of scope.querySelectorAll("[data-i18n]")) {
    const value = STRINGS[el.dataset.i18n];
    if (value !== undefined) el.textContent = value;
  }
  for (const el of scope.querySelectorAll("[data-i18n-placeholder]")) {
    const value = STRINGS[el.dataset.i18nPlaceholder];
    if (value !== undefined) el.placeholder = value;
  }
  for (const el of scope.querySelectorAll("[data-i18n-title]")) {
    const value = STRINGS[el.dataset.i18nTitle];
    if (value !== undefined) el.title = value;
  }
}

async function loadTranslations() {
  const payload = await call("get_translations");
  if (payload && payload.strings) {
    STRINGS = payload.strings;
    document.documentElement.lang = payload.language || "it";
    applyTranslations();
  }
}

/* ------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

async function call(name, ...args) {
  const bridge = api();
  if (!bridge || typeof bridge[name] !== "function") {
    log("activity-log", t("js.bridge_unavailable", { name: name }));
    return null;
  }
  try {
    return await bridge[name](...args);
  } catch (error) {
    log("activity-log", t("js.call_failed", { name: name, error: error }));
    return null;
  }
}

function text(value, fallback) {
  return value === null || value === undefined || value === "" ? fallback || "—" : String(value);
}

function log(id, line) {
  const box = $(id);
  if (!box) return;
  const stamp = new Date().toLocaleTimeString();
  box.textContent += (box.textContent ? "\n" : "") + "[" + stamp + "] " + line;
  box.scrollTop = box.scrollHeight;
}

function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function formatUptime(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return days + "g " + hours + "h";
  if (hours > 0) return hours + "h " + minutes + "m";
  return minutes + "m";
}

function setBusy(busy) {
  state.busy = busy;
  document.body.classList.toggle("busy", busy);
  document.querySelectorAll("button").forEach((button) => {
    if (button.dataset.keepEnabled === "1") return;
    button.disabled = busy;
  });
}

/* ---------------------------------------------------------- navigation */

const PAGE_TITLES = {
  dashboard: "ui.tab_dashboard",
  updates: "ui.tab_updates",
  settings: "ui.tab_settings",
  diag: "ui.tab_diagnostics",
  manager: "ui.tab_manager",
};

function goto(page, focus) {
  if (!["dashboard", "updates", "settings", "diag", "manager"].includes(page)) page = "dashboard";
  state.page = page;
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.id === "page-" + page);
  });
  document.querySelectorAll(".nav-item[data-page]").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === page);
  });
  const title = $("page-title");
  if (title) title.textContent = t(PAGE_TITLES[page]);
  document.querySelectorAll("details.menu[open]").forEach((menu) => menu.removeAttribute("open"));
  if (page === "manager") loadManager();
  if (page === "diag") loadDiag();
  if (page === "updates") renderUpdates();
  if (page === "settings") openSettings(focus || settings.instanceId);
}

/* ----------------------------------------------------------- dashboard */

function pidText(row) {
  if (!row.processes) return "—";
  const boot = row.processes.bootloader_pid;
  const app = row.processes.app_pid;
  return boot ? boot + " / " + app : String(app);
}

function renderInstancesTable() {
  const body = $("instances-body");
  body.textContent = "";
  if (!state.rows.length) {
    const tr = document.createElement("tr");
    tr.className = "empty";
    const td = document.createElement("td");
    td.colSpan = 12;
    td.textContent = t("js.no_instances");
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  state.rows.forEach((row) => {
    const tr = document.createElement("tr");

    const kind = document.createElement("td");
    const dot = document.createElement("span");
    dot.className = "dot " + (row.is_running ? "on" : "off");
    kind.appendChild(dot);
    kind.appendChild(document.createTextNode(text(row.kind_display, row.kind)));
    tr.appendChild(kind);

    const cells = [
      text(row.label),
      text(row.config_dir),
      text(row.local_version),
      null, // remote, built below
      formatDate(row.exe_mtime),
      pidText(row),
      formatUptime(row.uptime_seconds),
      text(row.settings_path),
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      if (index === 3) {
        if (row.update_available) {
          td.className = "tag-update";
          td.textContent = "↑ " + text(row.remote_version);
        } else {
          td.textContent = "—";
        }
      } else if (index === 1 || index === 7) {
        const span = document.createElement("span");
        span.className = "path";
        span.title = value;
        span.textContent = value;
        td.appendChild(span);
      } else {
        td.textContent = value;
      }
      tr.appendChild(td);
    });

    const autostart = document.createElement("td");
    autostart.appendChild(autostartToggle(row));
    tr.appendChild(autostart);

    const health = document.createElement("td");
    if (row.watchdog) {
      const badge = document.createElement("span");
      badge.className = "badge-warn";
      badge.textContent = row.watchdog in WATCHDOG_LABELS ? t(WATCHDOG_LABELS[row.watchdog]) : row.watchdog;
      badge.title = (watchdogEventsFor(row.id)[0] || {}).detail || "";
      health.appendChild(badge);
    } else {
      health.textContent = row.is_running ? "ok" : "—";
    }
    tr.appendChild(health);

    const actions = document.createElement("td");
    actions.className = "actions-col";
    const buttons = [
      [t("ui.restart_one"), () => act("restart", row.id), row.is_running],
      [t("ui.stop_one"), () => act("stop", row.id), row.is_running],
      [t("ui.start_one"), () => act("start", row.id), !row.is_running && Boolean(row.exe_path)],
      [t("ui.tab_settings"), () => showSettings(row.id), true],
      [t("ui.folder"), () => call("open_folder", row.config_dir), true],
    ];
    buttons.forEach(([label, handler, enabled]) => {
      const button = document.createElement("button");
      button.className = "btn small";
      button.textContent = label;
      button.disabled = !enabled;
      button.addEventListener("click", handler);
      actions.appendChild(button);
    });
    if (row.watchdog) {
      const repair = document.createElement("button");
      repair.className = "btn small";
      repair.textContent = t("ui.repair");
      repair.addEventListener("click", () => repairInstance(row.id));
      actions.appendChild(repair);
    }
    if (row.update_available) {
      const button = document.createElement("button");
      button.className = "btn small primary";
      button.textContent = t("ui.update");
      button.addEventListener("click", () => {
        selectUpdate(row.exe_path);
        goto("updates");
      });
      actions.appendChild(button);
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  });

}

function renderInstances() {
  const cards = state.view === "cards";
  $("instances-cards").hidden = !cards;
  $("instances-table").hidden = cards;
  document.querySelectorAll("#view-switch button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.view);
  });
  if (cards) renderInstanceCards();
  else renderInstancesTable();
  renderStats();
}

function watchdogEventsFor(id) {
  return (state.watchdog.events || []).filter((event) => event.id === id);
}

async function repairInstance(id) {
  setBusy(true);
  log("activity-log", "riparazione di " + id + "…");
  const result = await call("repair_instance", id);
  if (result) toast(text(result.detail, JSON.stringify(result)), result.ok === false ? "error" : "ok");
  setBusy(false);
  await refresh();
}

async function act(action, id) {
  setBusy(true);
  log("activity-log", action + " " + id + "…");
  const results = await call(action, [id]);
  reportResults(results);
  setBusy(false);
  await refresh();
}

async function bulk(action) {
  setBusy(true);
  log("activity-log", action + "…");
  const results = await call(action);
  reportResults(results);
  setBusy(false);
  await refresh();
}

function showSettings(id) {
  goto("settings", id);
}

async function refresh() {
  const rows = await call("list_instances", true);
  if (rows) state.rows = rows;
  const updates = await call("get_update_state");
  if (updates) state.updates = updates;
  const findings = await call("get_watchdog_state");
  state.watchdog = findings && findings.events ? findings : { count: 0, events: [] };
  const worst = {};
  (state.watchdog.events || []).forEach((event) => {
    if (!worst[event.id] || event.severity > worst[event.id].severity) worst[event.id] = event;
  });
  state.rows.forEach((row) => {
    row.watchdog = worst[row.id] ? worst[row.id].type : null;
  });
  renderInstances();
  renderUpdates();
}

/* --------------------------------------------------------- aggiornamenti */

function planList() {
  const plans = state.updates.plans || [];
  return plans.length ? plans : state.updates.available || [];
}

function renderUpdates() {
  const badge = $("nav-updates-badge");
  const own = state.updates.self_update;
  const count = (state.updates.available || []).length + (own && own.available ? 1 : 0);
  badge.hidden = !count;
  badge.textContent = String(count);
  const list = $("updates-list");
  list.textContent = "";
  const plans = planList();
  $("updates-status").textContent = state.updates.error
    ? t("js.check_failed", { error: state.updates.error })
    : plans.length || own || state.updates.last_check
      ? count === 1
        ? t("js.one_update_available")
        : t("js.updates_available", { count: count })
      : t("js.no_check_yet");

  if (own) {
    const button = document.createElement("button");
    button.className = "list-item";
    const title = document.createElement("div");
    title.textContent = "Usage Monitor Manager" + (own.available ? "  ↑" : "");
    if (own.available) title.className = "tag-update";
    const sub = document.createElement("div");
    sub.className = "sub";
    sub.textContent = own.target_version
      ? text(own.current_version, "?") + " → " + own.target_version
      : text(own.detail);
    const link = document.createElement("div");
    link.className = "sub";
    link.textContent = t("js.open_manager_update");
    button.appendChild(title);
    button.appendChild(sub);
    button.appendChild(link);
    button.addEventListener("click", () => goto("manager"));
    list.appendChild(button);
  }

  plans.forEach((plan) => {
    const button = document.createElement("button");
    button.className = "list-item" + (state.selected === plan.exe_path ? " active" : "");
    const title = document.createElement("div");
    const outdated = (state.updates.available || []).some((p) => p.exe_path === plan.exe_path);
    title.textContent = plan.kind + (outdated ? "  ↑" : "");
    if (outdated) title.className = "tag-update";
    const sub = document.createElement("div");
    sub.className = "sub";
    sub.textContent = text(plan.current_version, "?") + " → " + text(plan.target && plan.target.version);
    button.appendChild(title);
    button.appendChild(sub);
    button.addEventListener("click", () => selectUpdate(plan.exe_path));
    list.appendChild(button);
  });

  if (state.selected) renderDetail();
}

async function selectUpdate(exePath) {
  state.selected = exePath;
  await loadArchive();
  renderUpdates();
  const data = await call("get_changelog", exePath);
  if (!data || !data.tag) {
    $("changelog").textContent = t("js.no_changelog");
    toast(t("js.no_changelog"), "warn");
    return;
  }
  $("update-detail").hidden = false;
  $("update-detail-empty").hidden = true;
  $("update-title").textContent = data.kind_display + " " + text(data.version);
  $("update-meta").textContent =
    "repo " + data.repo + " · tag " + data.tag + " · pubblicata " + formatDate(data.published_at) +
    " · versione locale " + text(data.current_version, "?") + (data.is_prerelease ? " · prerelease" : "");
  $("changelog").innerHTML = renderMarkdown(data.body_markdown || "");
  renderDetail();
}

function renderDetail() {
  const outdated = (state.updates.available || []).some((plan) => plan.exe_path === state.selected);
  const skipped = (state.updates.skipped || []).some((plan) => plan.exe_path === state.selected);
  $("btn-apply").disabled = state.busy || !outdated;
  $("btn-skip").disabled = state.busy || !(outdated || skipped);
  $("btn-skip").textContent = skipped ? t("js.unskip") : t("ui.skip_version");
  renderArchive();
}

function renderArchive() {
  const host = $("archive-list");
  if (!host) return;
  host.textContent = "";
  if (!state.archived.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = t("js.no_archived_build");
    host.appendChild(empty);
    return;
  }
  state.archived.forEach((item) => {
    const row = document.createElement("div");
    row.className = "archive-row";
    const label = document.createElement("span");
    label.className = "grow";
    label.textContent =
      item.version +
      " · " +
      formatDate(item.archived_at) +
      (item.source_release_tag ? " · " + item.source_release_tag : "");
    const button = document.createElement("button");
    button.className = "btn small";
    button.textContent = t("ui.restore");
    button.disabled = state.busy || !item.exists;
    button.addEventListener("click", () => restoreArchived(item.version));
    row.appendChild(label);
    row.appendChild(button);
    host.appendChild(row);
  });
}

async function loadArchive() {
  if (!state.selected) {
    state.archived = [];
    return;
  }
  const items = await call("list_archived", state.selected);
  state.archived = items || [];
}

async function restoreArchived(version) {
  if (!state.selected) return;
  setBusy(true);
  log("update-log", "ripristino della " + version + "…");
  const result = await call("restore_archived", state.selected, version);
  if (result) reportOutcome("update-log", result);
  setBusy(false);
  await loadArchive();
  await refresh();
  renderDetail();
}

async function skipSelected() {
  const plan = planList().find((item) => item.exe_path === state.selected);
  if (!plan) return;
  const skipped = (state.updates.skipped || []).some((item) => item.exe_path === state.selected);
  setBusy(true);
  const result = await call(skipped ? "unskip_version" : "skip_version", plan.kind);
  if (result) toast(text(result.detail, JSON.stringify(result)), "ok");
  setBusy(false);
  await refresh();
  renderDetail();
}

async function applyUpdate() {
  if (!state.selected) return;
  setBusy(true);
  $("progress").hidden = false;
  $("progress-fill").style.width = "0%";
  log("update-log", t("js.updating", { target: state.selected }));
  const result = await call("apply_update", state.selected);
  if (result) reportOutcome("update-log", result);
  $("progress").hidden = true;
  setBusy(false);
  await refresh();
}

async function rollback() {
  if (!state.selected) return;
  setBusy(true);
  log("update-log", "rollback di " + state.selected);
  const result = await call("rollback", state.selected);
  if (result) reportOutcome("update-log", result);
  setBusy(false);
  await refresh();
}

/* --------------------------------------------------------- diagnostics */

/* PLAN.md section 9.7: one instance at a time runs with --verbose and its
 * output lands in a file; this page starts and stops that mode, tails the log
 * and assembles the block to paste in a GitHub issue. */

async function loadDiag() {
  const picker = $("diag-instance");
  const current = picker.value;
  picker.innerHTML = "";
  state.rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.id;
    option.textContent = (row.label || row.kind_display || row.id) + (row.is_running ? " (in esecuzione)" : "");
    picker.appendChild(option);
  });
  if (current) picker.value = current;
  await refreshDiag();
}

async function refreshDiag() {
  const lines = Number($("diag-lines").value) || 200;
  const data = await call("diag_state", lines);
  if (!data) return;
  state.diag.session = data.session;
  const source = $("diag-source");
  const wanted = state.diag.source || (data.session ? data.session.log_path : data.manager_log);
  source.innerHTML = "";
  const options = [];
  (data.logs || []).forEach((path) => options.push(path));
  if (data.manager_log) options.push(data.manager_log);
  options.forEach((path) => {
    const option = document.createElement("option");
    option.value = path;
    option.textContent = path;
    source.appendChild(option);
  });
  if (options.includes(wanted)) source.value = wanted;
  state.diag.source = source.value;

  $("diag-state").textContent = data.session
    ? t("js.diag_active", { instance: data.session.instance_id, path: data.session.log_path })
    : t("js.diag_none");
  if (data.session && source.value === data.session.log_path) {
    $("diag-log").textContent = (data.lines || []).join("\n");
  } else {
    const tail = await call("diag_log", source.value, lines);
    if (tail) $("diag-log").textContent = (tail.lines || []).join("\n");
  }
}

async function startDiag() {
  const id = $("diag-instance").value;
  if (!id) return;
  setBusy(true);
  const result = await call("diag_start", id);
  setBusy(false);
  if (result) toast(text(result.detail), result.ok === false ? "error" : "ok");
  await refresh();
  await loadDiag();
}

async function stopDiag() {
  const id = (state.diag.session && state.diag.session.instance_id) || $("diag-instance").value;
  if (!id) return;
  setBusy(true);
  const result = await call("diag_stop", id);
  setBusy(false);
  if (result) toast(text(result.detail), result.ok === false ? "error" : "ok");
  await refresh();
  await loadDiag();
}

async function copyDiagReport() {
  const id = (state.diag.session && state.diag.session.instance_id) || $("diag-instance").value;
  const lines = Number($("diag-lines").value) || 200;
  const result = await call("diag_report", id, lines);
  if (result && result.text) copyText(result.text, t("js.diag_copied"));
  else toast(t("js.copy_failed"), "error");
}

function copyText(value, message) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(value).then(
      () => toast(message, "ok"),
      () => toast(t("js.copy_failed"), "error")
    );
    return;
  }
  toast(t("js.copy_unavailable"), "warn");
}

function toggleFollow() {
  if (state.diag.timer) {
    clearInterval(state.diag.timer);
    state.diag.timer = null;
  }
  if ($("diag-follow").checked) {
    state.diag.timer = setInterval(refreshDiag, 2000);
  }
}

/* ------------------------------------------------------------- manager */

async function loadManager() {
  const info = await call("get_manager_info");
  if (info) {
    $("header-version").textContent = info.version;
    $("manager-version").textContent = t("js.manager_version", { version: info.version });
    $("manager-exe").textContent = (info.frozen ? "eseguibile: " : "da sorgente: ") + info.executable;
    $("manager-config-path").textContent = info.config_path;
    $("pref-autostart").checked = Boolean(info.autostart);
    state.manager = info;
    renderToken(info);
    renderSelfUpdate(info.self_update);
  }
  const config = await call("get_config");
  if (config && config.preferences) {
    $("pref-notify").checked = Boolean(config.preferences.notify_updates);
    $("pref-prerelease").checked = Boolean(config.preferences.include_prerelease);
    $("pref-interval").value = config.preferences.check_interval_hours;
    $("pref-watchdog").checked = Boolean(config.preferences.watchdog_enabled);
    $("pref-auto-restart").checked = Boolean(config.preferences.auto_restart_crashed);
    $("pref-archive-keep").value = config.preferences.archive_keep;
    $("pref-hotkey").value = config.preferences.hotkey || "";
    $("pref-status-file").value = config.preferences.status_file || "";
    $("hotkey-state").textContent = config.preferences.hotkey
      ? t("js.hotkey_next_start")
      : t("js.hotkey_none");
    $("status-file-state").textContent = config.preferences.status_file
      ? t("js.status_file_on")
      : t("js.disabled_f");
  }
  await loadKinds();
  await loadProfiles();
  await refreshManagerLog();
}

async function refreshManagerLog() {
  const data = await call("get_manager_log", 200);
  if (!data) return;
  $("manager-log-path").textContent = data.path;
  $("manager-log").textContent = (data.lines || []).join("\n");
}

/* ------------------------------------------------- kinds and profiles */

async function loadKinds() {
  const rows = await call("list_kinds");
  if (!rows) return;
  state.kinds = rows;
  const body = $("kinds-body");
  body.innerHTML = "";
  rows.forEach((kind) => {
    const tr = document.createElement("tr");
    [kind.key, kind.family, kind.exe_name, kind.repo, kind.source].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = text(value);
      tr.appendChild(td);
    });
    const actions = document.createElement("td");
    if (kind.source === "custom") {
      const remove = document.createElement("button");
      remove.className = "btn danger";
      remove.textContent = t("ui.remove");
      remove.addEventListener("click", () => removeKind(kind.key));
      actions.appendChild(remove);
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  });
}

async function addKind() {
  const payload = {
    key: $("kind-key").value,
    display_name: $("kind-name").value || $("kind-key").value,
    exe_name: $("kind-exe").value,
    repo: $("kind-repo").value,
    asset_pattern: $("kind-asset").value,
    sha_sidecar: $("kind-sidecar").checked,
  };
  setBusy(true);
  const result = await call("add_kind", payload);
  setBusy(false);
  if (!result) return;
  $("kinds-state").textContent = result.ok ? t("js.kind_added") : (result.problems || []).join(" · ");
  if (result.ok) {
    ["kind-key", "kind-name", "kind-exe", "kind-repo", "kind-asset"].forEach((id) => ($(id).value = ""));
    await loadKinds();
    await refresh();
  }
}

async function removeKind(key) {
  setBusy(true);
  const result = await call("remove_kind", key);
  setBusy(false);
  $("kinds-state").textContent = result && result.removed ? t("js.kind_removed") : t("js.no_custom_kind");
  await loadKinds();
  await refresh();
}

async function loadProfiles() {
  const rows = await call("list_profiles");
  if (!rows) return;
  state.profiles = rows;
  const body = $("profiles-body");
  body.innerHTML = "";
  rows.forEach((profile) => {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = profile.name;
    const members = document.createElement("td");
    members.textContent = (profile.instance_ids || []).join(", ") || "—";
    const actions = document.createElement("td");
    const apply = document.createElement("button");
    apply.className = "btn primary";
    apply.textContent = t("ui.apply");
    apply.addEventListener("click", () => applyProfile(profile.name));
    const remove = document.createElement("button");
    remove.className = "btn danger";
    remove.textContent = t("ui.remove");
    remove.addEventListener("click", () => deleteProfile(profile.name));
    actions.appendChild(apply);
    actions.appendChild(remove);
    tr.appendChild(name);
    tr.appendChild(members);
    tr.appendChild(actions);
    body.appendChild(tr);
  });
  const picker = $("profile-instances");
  picker.innerHTML = "";
  state.rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.id;
    option.textContent = row.label || row.kind_display || row.id;
    picker.appendChild(option);
  });
}

async function saveProfile() {
  const name = $("profile-name").value.trim();
  const ids = Array.from($("profile-instances").selectedOptions).map((option) => option.value);
  if (!name || !ids.length) {
    $("profiles-state").textContent = t("js.profile_needs_members");
    return;
  }
  setBusy(true);
  const result = await call("save_profile", name, ids);
  setBusy(false);
  $("profiles-state").textContent = result && result.ok ? t("js.profile_saved") : text(result && result.detail);
  $("profile-name").value = "";
  await loadProfiles();
}

async function deleteProfile(name) {
  setBusy(true);
  await call("delete_profile", name);
  setBusy(false);
  await loadProfiles();
}

async function applyProfile(name) {
  const preview = await call("profile_plan", name);
  if (preview && preview.ok && !confirm((preview.steps || []).join("\n"))) return;
  setBusy(true);
  const result = await call("apply_profile", name);
  setBusy(false);
  if (result) {
    toast(t(result.ok ? "js.profile_applied" : "js.profile_errors", { name: name }), result.ok ? "ok" : "error");
  }
  await refresh();
}

/* ------------------------------------------------- hotkey, status file */

async function saveHotkey(value) {
  const result = await call("set_hotkey", value);
  if (!result) return;
  $("hotkey-state").textContent = result.ok
    ? t("js.hotkey_saved")
    : t("js.hotkey_invalid", { detail: text(result.detail) });
  if (result.ok) $("pref-hotkey").value = result.hotkey || "";
}

function captureHotkey() {
  const field = $("pref-hotkey");
  $("hotkey-state").textContent = "premi la combinazione…";
  const onKey = (event) => {
    event.preventDefault();
    const key = event.key.toLowerCase();
    if (["control", "alt", "shift", "meta"].includes(key)) return;
    const parts = [];
    if (event.ctrlKey) parts.push("ctrl");
    if (event.altKey) parts.push("alt");
    if (event.shiftKey) parts.push("shift");
    if (event.metaKey) parts.push("win");
    parts.push(key === " " ? "space" : key);
    field.value = parts.join("+");
    window.removeEventListener("keydown", onKey, true);
    saveHotkey(field.value);
  };
  window.addEventListener("keydown", onKey, true);
}

async function saveStatusFile(value) {
  const result = await call("set_status_file", value);
  if (!result) return;
  $("status-file-state").textContent = result.ok
    ? value
      ? t("js.status_file_written", { path: text(result.written) })
      : t("js.disabled_f")
    : t("js.status_file_failed", { detail: text(result.detail) });
}

function renderToken(info) {
  const stored = info.github_token_stored;
  const source = info.github_token_source;
  $("github-token").textContent = info.github_token_present
    ? t("js.token_present") +
      (stored ? t("js.token_from_store") : t("js.token_from_env", { name: source }))
    : t("js.token_absent");
  const rate = info.rate_limit;
  $("github-rate").textContent = rate
    ? t("js.rate_limit", {
        remaining: text(rate.remaining),
        limit: text(rate.limit),
        reset: formatDate(rate.reset_at),
      })
    : t("js.rate_limit_unknown");
  $("github-target").textContent = t("js.credential_target", { target: text(info.credential_target) });
  $("btn-token-clear").disabled = !stored;
}

function renderSelfUpdate(own) {
  const box = $("self-update-state");
  const version = state.manager ? state.manager.version : "";
  if (!own) {
    box.textContent = t("js.manager_no_check", { version: version });
  } else {
    box.textContent = t("js.manager_detail", { version: version, detail: text(own.detail) });
  }
  const available = Boolean(own && own.available);
  const frozen = Boolean(state.manager && state.manager.frozen);
  $("btn-self-apply").disabled = state.busy || !available || !frozen;
  $("btn-self-apply").title = frozen ? "" : t("js.self_update_frozen_only");
  $("btn-self-skip").disabled = state.busy || !(own && own.tag);
  $("btn-self-skip").textContent = own && own.skipped ? t("js.unskip") : t("ui.skip_version");
}

async function setToken() {
  const field = $("token-input");
  const value = field.value;
  if (!value.trim()) {
    log("activity-log", t("js.token_field_empty"));
    return;
  }
  setBusy(true);
  const result = await call("set_github_token", value);
  field.value = ""; // the value is never shown again, here or anywhere else
  if (result) toast(text(result.detail), result.ok === false ? "error" : "ok");
  setBusy(false);
  await loadManager();
}

async function clearToken() {
  setBusy(true);
  const result = await call("clear_github_token");
  if (result) toast(text(result.detail), result.ok === false ? "error" : "ok");
  setBusy(false);
  await loadManager();
}

async function refreshRateLimit() {
  const result = await call("get_rate_limit");
  if (result && state.manager) {
    state.manager.rate_limit = result.rate_limit;
    state.manager.github_token_stored = result.token_present;
    state.manager.github_token_source = result.token_source;
    renderToken(state.manager);
  }
}

async function checkSelfUpdate() {
  setBusy(true);
  const own = await call("check_self_update");
  if (state.manager) state.manager.self_update = own;
  renderSelfUpdate(own);
  setBusy(false);
}

async function applySelfUpdate() {
  setBusy(true);
  $("self-progress").hidden = false;
  $("self-progress-fill").style.width = "0%";
  log("self-update-log", t("js.self_updating"));
  const result = await call("apply_self_update");
  if (result) reportOutcome("self-update-log", result);
  $("self-progress").hidden = true;
  setBusy(false);
}

async function skipSelfUpdate() {
  const own = state.manager ? state.manager.self_update : null;
  const result = await call(own && own.skipped ? "unskip_version" : "skip_version", "manager");
  if (result) log("activity-log", text(result.detail, JSON.stringify(result)));
  await loadManager();
}

async function setPreference(key, value) {
  const result = await call("set_preference", key, value);
  const failed = result && result.ok === false;
  toast(
    t("js.preference_set", { key: key, value: value }) + (failed ? t("js.preference_not_saved") : ""),
    failed ? "error" : "ok"
  );
}

async function runImport(dryRun) {
  const report = await call("import_legacy", dryRun);
  const box = $("import-log");
  box.textContent = report ? (report.lines || []).join("\n") : t("js.import_failed");
  if (!dryRun) await refresh();
}

/* ------------------------------------------------------- impostazioni
 *
 * The form is generated from the schema returned by get_settings(): one
 * control per key, grouped in collapsible sections in schema.section_order.
 * The draft lives in settings.draft and is the single source of truth - the
 * JSON tab, the diff and the validation all read it, and nothing is written
 * until Salva.
 */

const settings = {
  instanceId: null,
  payload: null,
  schema: null,
  saved: {},
  draft: {},
  subtab: "form",
  search: "",
  historyStamp: null,
  presets: [],
};

const SETTINGS_SUBTABS = ["form", "json", "unknown", "history"];

function settingsLog(line) {
  log("settings-log", line);
}

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function fieldFor(key) {
  if (!settings.schema) return null;
  const exact = settings.schema.fields.find((entry) => entry.key === key);
  if (exact) return exact;
  return (
    settings.schema.fields.find(
      (entry) => entry.type === "prefix_family" && key !== entry.key && key.startsWith(entry.key)
    ) || null
  );
}

function knownKeys() {
  return new Set(settings.schema ? settings.schema.fields.map((entry) => entry.key) : []);
}

function unknownKeys() {
  return Object.keys(settings.draft).filter((key) => !fieldFor(key));
}

/* ---- value <-> text conversions, one pair per editor type ---- */

function valueToText(field, value) {
  if (value === undefined || value === null) return "";
  switch (field.type) {
    case "string":
    case "hex_color":
    case "enum":
      return String(value);
    case "int":
    case "percent":
      return String(value);
    case "command":
      return Array.isArray(value) ? value.join(" ‖ ") : String(value);
    case "string_list":
    case "wildcard_list":
    case "icon_fields":
    case "threshold_list":
    case "amount_list":
    case "prefix_family":
      return Array.isArray(value) ? value.join(", ") : JSON.stringify(value);
    default:
      return JSON.stringify(value, null, 2);
  }
}

function textToValue(field, text) {
  const trimmed = String(text).trim();
  switch (field.type) {
    case "int":
    case "percent": {
      if (trimmed === "") return undefined;
      const number = Number(trimmed);
      return Number.isNaN(number) ? trimmed : number;
    }
    case "command": {
      if (trimmed === "") return [];
      if (trimmed.startsWith("[")) return JSON.parse(trimmed);
      return trimmed.split("‖").map((item) => item.trim()).filter(Boolean);
    }
    case "threshold_list":
    case "amount_list":
    case "prefix_family": {
      if (trimmed === "") return [];
      return trimmed.split(",").map((item) => {
        const number = Number(item.trim());
        return Number.isNaN(number) ? item.trim() : number;
      });
    }
    case "string_list":
    case "wildcard_list":
    case "icon_fields": {
      if (trimmed === "") return [];
      return trimmed.split(",").map((item) => item.trim()).filter(Boolean);
    }
    case "rgba_dict":
    case "cli_command_map":
      return JSON.parse(trimmed || "{}");
    default:
      return text;
  }
}

/* ---- draft mutation ---- */

function setKey(key, value) {
  if (value === undefined) delete settings.draft[key];
  else settings.draft[key] = value;
  refreshSettingsDerived();
}

function refreshSettingsDerived() {
  renderJsonTab();
  renderUnknownTab();
  renderPreview();
  validateDraftNow();
}

let validateTimer = null;
function validateDraftNow() {
  if (validateTimer) clearTimeout(validateTimer);
  validateTimer = setTimeout(async () => {
    if (!settings.schema) return;
    const report = await call("validate_draft", settings.schema.kind, settings.draft);
    applyValidation(report);
  }, 150);
}

function applyValidation(report) {
  const errors = (report && report.errors) || [];
  const byKey = new Map();
  errors.forEach((entry) => {
    const root = entry.key.split(".")[0];
    byKey.set(root, entry.message);
  });
  document.querySelectorAll("[data-settings-key]").forEach((node) => {
    const key = node.dataset.settingsKey;
    const message = byKey.get(key);
    node.classList.toggle("invalid", Boolean(message));
    const slot = node.querySelector(".field-error");
    if (slot) slot.textContent = message || "";
  });
  $("settings-state").textContent = errors.length
    ? t("js.invalid_values", { count: errors.length })
    : t("js.unsaved_changes", { count: dirtyCount() });
}

function dirtyCount() {
  const keys = new Set(Object.keys(settings.saved).concat(Object.keys(settings.draft)));
  let count = 0;
  keys.forEach((key) => {
    if (!sameJson(settings.saved[key], settings.draft[key])) count += 1;
  });
  return count;
}

/* ---- the form ---- */

function buildControl(field) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  wrap.dataset.settingsKey = field.key;

  const head = document.createElement("div");
  head.className = "field-head";
  const name = document.createElement("code");
  name.textContent = field.key + (field.deprecated ? " (obsoleta)" : "");
  head.appendChild(name);

  const present = document.createElement("label");
  present.className = "field-toggle";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = Object.prototype.hasOwnProperty.call(settings.draft, field.key);
  present.appendChild(box);
  present.appendChild(document.createTextNode("impostata"));
  head.appendChild(present);

  const reset = document.createElement("button");
  reset.className = "btn small";
  reset.textContent = t("ui.reset_to_default");
  reset.addEventListener("click", () => {
    setKey(field.key, clone(field.default));
    renderForm();
  });
  head.appendChild(reset);
  wrap.appendChild(head);

  const value = settings.draft[field.key];
  const editor = document.createElement("div");
  editor.className = "field-editor";

  const commit = (newValue) => {
    box.checked = true;
    setKey(field.key, newValue);
  };

  if (field.type === "bool") {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = value === undefined ? Boolean(field.default) : Boolean(value);
    input.addEventListener("change", () => commit(input.checked));
    editor.appendChild(input);
  } else if (field.type === "enum") {
    const select = document.createElement("select");
    field.choices.forEach((choice) => {
      const option = document.createElement("option");
      option.value = choice;
      option.textContent = choice === "" ? "(automatico)" : choice;
      select.appendChild(option);
    });
    select.value = value === undefined ? String(field.default) : String(value);
    select.addEventListener("change", () => commit(select.value));
    editor.appendChild(select);
  } else if (field.type === "rgba_dict" || field.type === "cli_command_map") {
    const area = document.createElement("textarea");
    area.rows = field.type === "rgba_dict" ? 6 : 4;
    area.value = JSON.stringify(value === undefined ? field.default : value, null, 2);
    area.addEventListener("change", () => {
      try {
        commit(JSON.parse(area.value || "{}"));
        area.classList.remove("invalid");
      } catch (error) {
        area.classList.add("invalid");
        settingsLog(t("js.invalid_json_for", { key: field.key, error: error }));
      }
    });
    editor.appendChild(area);
  } else {
    const input = document.createElement("input");
    input.type = "text";
    input.value = valueToText(field, value === undefined ? field.default : value);
    if (field.suggestions && field.suggestions.length) {
      input.setAttribute("list", "list-" + field.key);
      const list = document.createElement("datalist");
      list.id = "list-" + field.key;
      field.suggestions.forEach((suggestion) => {
        const option = document.createElement("option");
        option.value = suggestion;
        list.appendChild(option);
      });
      editor.appendChild(list);
    }
    input.addEventListener("change", () => {
      try {
        commit(textToValue(field, input.value));
        input.classList.remove("invalid");
      } catch (error) {
        input.classList.add("invalid");
        settingsLog(t("js.unparsable_value", { key: field.key, error: error }));
      }
    });
    editor.appendChild(input);
    if (field.type === "hex_color") {
      const picker = document.createElement("input");
      picker.type = "color";
      const current = String(value === undefined ? field.default : value);
      if (/^#[0-9a-fA-F]{6}$/.test(current)) picker.value = current;
      picker.addEventListener("change", () => {
        input.value = picker.value;
        commit(picker.value);
      });
      editor.appendChild(picker);
    }
  }

  box.addEventListener("change", () => {
    if (box.checked) setKey(field.key, clone(field.default));
    else setKey(field.key, undefined);
    renderForm();
  });

  wrap.appendChild(editor);

  const help = document.createElement("p");
  help.className = "hint";
  help.textContent = field.help;
  wrap.appendChild(help);

  const error = document.createElement("p");
  error.className = "field-error";
  wrap.appendChild(error);
  return wrap;
}

function buildFamily(field) {
  /* alert_thresholds_<campo>: the explicit keys have their own control, this
   * one edits the extra variants and can add a new key. */
  const wrap = document.createElement("div");
  wrap.className = "field family";
  wrap.dataset.settingsKey = field.key;

  const head = document.createElement("div");
  head.className = "field-head";
  const name = document.createElement("code");
  name.textContent = field.key + "<campo>";
  head.appendChild(name);
  wrap.appendChild(head);

  const known = knownKeys();
  Object.keys(settings.draft)
    .filter((key) => key.startsWith(field.key) && !known.has(key))
    .sort()
    .forEach((key) => {
      const row = document.createElement("div");
      row.className = "field-editor";
      const label = document.createElement("code");
      label.textContent = key;
      const input = document.createElement("input");
      input.type = "text";
      input.value = valueToText(field, settings.draft[key]);
      input.addEventListener("change", () => setKey(key, textToValue(field, input.value)));
      const remove = document.createElement("button");
      remove.className = "btn small danger";
      remove.textContent = t("ui.remove");
      remove.addEventListener("click", () => {
        setKey(key, undefined);
        renderForm();
      });
      row.appendChild(label);
      row.appendChild(input);
      row.appendChild(remove);
      wrap.appendChild(row);
    });

  const adder = document.createElement("div");
  adder.className = "field-editor";
  const picker = document.createElement("input");
  picker.type = "text";
  picker.placeholder = field.key + "seven_day_opus";
  picker.setAttribute("list", "list-family");
  const list = document.createElement("datalist");
  list.id = "list-family";
  (field.suggestions || []).forEach((suggestion) => {
    const option = document.createElement("option");
    option.value = suggestion;
    list.appendChild(option);
  });
  const add = document.createElement("button");
  add.className = "btn small";
  add.textContent = t("ui.add");
  add.addEventListener("click", () => {
    const key = picker.value.trim();
    if (!key.startsWith(field.key)) {
      settingsLog("la chiave deve iniziare con " + field.key);
      return;
    }
    setKey(key, []);
    renderForm();
  });
  adder.appendChild(list);
  adder.appendChild(picker);
  adder.appendChild(add);
  wrap.appendChild(adder);

  const help = document.createElement("p");
  help.className = "hint";
  help.textContent = field.help;
  wrap.appendChild(help);
  return wrap;
}

function renderForm() {
  const host = $("settings-form");
  host.textContent = "";
  if (!settings.schema) return;
  const needle = settings.search.trim().toLowerCase();
  const bySection = new Map();
  settings.schema.fields.forEach((field) => {
    if (needle && !(field.key + " " + field.help).toLowerCase().includes(needle)) return;
    if (!bySection.has(field.section)) bySection.set(field.section, []);
    bySection.get(field.section).push(field);
  });

  settings.schema.section_order.forEach((section) => {
    const fields = bySection.get(section);
    if (!fields || !fields.length) return;
    const details = document.createElement("details");
    details.open = Boolean(needle) || section === "alert";
    const summary = document.createElement("summary");
    summary.textContent = settings.schema.section_labels[section] || section;
    details.appendChild(summary);
    fields.forEach((field) => {
      details.appendChild(field.type === "prefix_family" ? buildFamily(field) : buildControl(field));
    });
    host.appendChild(details);
  });
  validateDraftNow();
}

/* ---- JSON tab, unknown keys, diff ---- */

function renderJsonTab() {
  const area = $("settings-json");
  if (document.activeElement !== area) area.value = JSON.stringify(settings.draft, null, 2);
  renderDiff();
}

function diffRows(before, after) {
  const keys = Array.from(new Set(Object.keys(before).concat(Object.keys(after)))).sort();
  const rows = [];
  keys.forEach((key) => {
    const inBefore = Object.prototype.hasOwnProperty.call(before, key);
    const inAfter = Object.prototype.hasOwnProperty.call(after, key);
    if (inBefore && !inAfter) rows.push({ key, change: "removed", before: before[key] });
    else if (!inBefore && inAfter) rows.push({ key, change: "added", after: after[key] });
    else if (!sameJson(before[key], after[key]))
      rows.push({ key, change: "changed", before: before[key], after: after[key] });
  });
  return rows;
}

function renderDiffInto(host, rows) {
  host.textContent = "";
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = t("js.no_difference");
    host.appendChild(empty);
    return;
  }
  const table = document.createElement("table");
  table.className = "diff";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.className = "diff-" + row.change;
    const key = document.createElement("td");
    key.textContent = row.key;
    const change = document.createElement("td");
    change.textContent = row.change;
    const before = document.createElement("td");
    before.textContent = row.before === undefined ? "—" : JSON.stringify(row.before);
    const after = document.createElement("td");
    after.textContent = row.after === undefined ? "—" : JSON.stringify(row.after);
    [key, change, before, after].forEach((cell) => tr.appendChild(cell));
    table.appendChild(tr);
  });
  host.appendChild(table);
}

function renderDiff() {
  renderDiffInto($("settings-diff"), diffRows(settings.saved, settings.draft));
}

function renderUnknownTab() {
  const host = $("settings-unknown");
  host.textContent = "";
  const keys = unknownKeys();
  if (!keys.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = t("js.no_unknown_key");
    host.appendChild(empty);
    return;
  }
  keys.sort().forEach((key) => {
    const row = document.createElement("div");
    row.className = "field";
    const label = document.createElement("code");
    label.textContent = key;
    const area = document.createElement("textarea");
    area.rows = 2;
    area.value = JSON.stringify(settings.draft[key], null, 2);
    area.addEventListener("change", () => {
      try {
        setKey(key, JSON.parse(area.value));
        area.classList.remove("invalid");
      } catch (error) {
        area.classList.add("invalid");
        settingsLog(t("js.invalid_json_for", { key: key, error: error }));
      }
    });
    const remove = document.createElement("button");
    remove.className = "btn small danger";
    remove.textContent = t("ui.remove");
    remove.addEventListener("click", () => {
      setKey(key, undefined);
      renderUnknownTab();
    });
    row.appendChild(label);
    row.appendChild(area);
    row.appendChild(remove);
    host.appendChild(row);
  });
}

/* ---- preview and presets ---- */

function rgbaToCss(value, fallback) {
  if (!Array.isArray(value) || value.length < 3) return fallback;
  return "rgba(" + value[0] + "," + value[1] + "," + value[2] + "," + (value.length > 3 ? value[3] / 255 : 1) + ")";
}

function renderPreview() {
  const bar = settings.draft.bar_fg || "#4a9eff";
  $("preview-bar-fill").style.background = bar;
  const light = (settings.draft.icon_light || {}).fg;
  const dark = (settings.draft.icon_dark || {}).fg;
  $("preview-icon-light").style.color = rgbaToCss(light, "#ffffff");
  $("preview-icon-dark").style.color = rgbaToCss(dark, "#000000");
}

async function loadPresets() {
  const presets = await call("get_presets");
  if (!presets) return;
  settings.presets = presets;
  const select = $("settings-preset");
  select.textContent = "";
  presets.forEach((preset) => {
    const option = document.createElement("option");
    option.value = preset.color;
    option.textContent = preset.name + " (" + preset.color + ")";
    select.appendChild(option);
  });
  const swatches = $("settings-swatches");
  swatches.textContent = "";
  presets.forEach((preset) => {
    const dot = document.createElement("button");
    dot.className = "swatch";
    dot.title = preset.name + " " + preset.color;
    dot.style.background = preset.color;
    dot.addEventListener("click", () => {
      select.value = preset.color;
      applyPreset(preset.color);
    });
    swatches.appendChild(dot);
  });
}

async function applyPreset(color) {
  if (!settings.instanceId) return;
  const result = await call("apply_preset", settings.instanceId, color, settings.draft);
  if (!result || result.ok === false) {
    settingsLog("preset: " + text(result && result.detail));
    return;
  }
  settings.draft = result.data;
  settingsLog("preset " + color + " applicato (bar " + result.palette.bar + ")");
  renderForm();
  refreshSettingsDerived();
}

/* ---- history ---- */

function renderHistory() {
  const host = $("settings-history");
  host.textContent = "";
  const versions = (settings.payload && settings.payload.history) || [];
  if (!versions.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = t("js.no_archived_version");
    host.appendChild(empty);
    return;
  }
  versions.forEach((version) => {
    const button = document.createElement("button");
    button.className = "list-item" + (settings.historyStamp === version.stamp ? " active" : "");
    const title = document.createElement("div");
    title.textContent = formatDate(version.saved_at) || version.stamp;
    const sub = document.createElement("div");
    sub.className = "sub";
    sub.textContent = version.size + " byte";
    button.appendChild(title);
    button.appendChild(sub);
    button.addEventListener("click", () => selectVersion(version.stamp));
    host.appendChild(button);
  });
}

async function selectVersion(stamp) {
  settings.historyStamp = stamp;
  renderHistory();
  const result = await call("get_history_version", settings.instanceId, stamp);
  if (!result || result.ok === false) {
    settingsLog("storico: " + text(result && result.detail));
    return;
  }
  $("settings-history-title").textContent = t("js.version_stamp", { stamp: stamp });
  renderDiffInto($("settings-history-diff"), result.diff.map((line) => line));
}

async function restoreVersion() {
  if (!settings.instanceId || !settings.historyStamp) return;
  if (!window.confirm(t("js.confirm_restore", { stamp: settings.historyStamp }))) return;
  const result = await call("restore_history", settings.instanceId, settings.historyStamp);
  if (!result || result.ok === false) {
    settingsLog("ripristino: " + text(result && result.detail));
    return;
  }
  applySettingsPayload(result);
  settingsLog(text(result.detail));
}

/* ---- page plumbing ---- */

function applySettingsPayload(payload) {
  settings.payload = payload;
  settings.schema = payload.schema || settings.schema;
  settings.saved = clone(payload.data) || {};
  settings.draft = clone(payload.data) || {};
  $("settings-file").textContent = payload.path + (payload.exists ? "" : t("js.file_missing_suffix"));
  $("settings-shadow").textContent =
    payload.shadowed && payload.shadowed.length
      ? t("js.shadowed_files", { files: payload.shadowed.join(", ") })
      : "";
  $("btn-settings-create").disabled = Boolean(payload.exists);
  $("btn-settings-independent").hidden = !payload.can_create_independent;
  $("settings-independent-hint").hidden = !payload.can_create_independent;
  $("settings-independent-hint").textContent = payload.can_create_independent
    ? t("js.independent_settings_hint", { path: payload.independent_path })
    : "";
  if (payload.error) settingsLog(t("js.read_error", { error: payload.error }));
  renderForm();
  renderHistory();
  refreshSettingsDerived();
}

function renderInstancePicker() {
  const select = $("settings-instance");
  const previous = settings.instanceId;
  select.textContent = "";
  state.rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.id;
    option.textContent = (row.label || row.kind_display) + " — " + row.config_dir;
    select.appendChild(option);
  });
  if (previous) select.value = previous;
}

async function openSettings(instanceId) {
  if (!state.rows.length) await refresh();
  renderInstancePicker();
  settings.instanceId = instanceId || $("settings-instance").value || (state.rows[0] && state.rows[0].id);
  if (!settings.instanceId) return;
  $("settings-instance").value = settings.instanceId;
  const payload = await call("get_settings", settings.instanceId);
  if (!payload || payload.ok === false) {
    settingsLog("apertura: " + text(payload && payload.detail));
    return;
  }
  applySettingsPayload(payload);
}

function showSubtab(name) {
  settings.subtab = SETTINGS_SUBTABS.includes(name) ? name : "form";
  SETTINGS_SUBTABS.forEach((tab) => {
    $("settings-sub-" + tab).hidden = tab !== settings.subtab;
  });
  document.querySelectorAll("#settings-tabs .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.subtab === settings.subtab);
  });
}

async function saveSettings(restart) {
  if (!settings.instanceId) return;
  if (restart && !window.confirm(t("js.confirm_save_restart"))) return;
  setBusy(true);
  const result = await call("save_settings", settings.instanceId, settings.draft, Boolean(restart));
  setBusy(false);
  if (!result || result.ok === false) {
    settingsLog(t("js.save_failed", { detail: text(result && result.detail) }));
    toast(t("js.save_failed", { detail: text(result && result.detail) }), "error");
    if (result && result.validation) applyValidation(result.validation);
    return;
  }
  applySettingsPayload(result);
  settingsLog(text(result.detail));
  toast(text(result.detail), "ok");
  await refresh();
}

async function copyFromInstance() {
  if (!settings.instanceId || !settings.schema) return;
  const others = state.rows.filter(
    (row) => row.id !== settings.instanceId && row.kind === settings.schema.kind
  );
  if (!others.length) {
    settingsLog(t("js.no_other_instance"));
    return;
  }
  const source = window.prompt(
    t("js.prompt_copy_from") + "\n" + others.map((row) => row.id).join("\n"),
    others[0].id
  );
  if (!source) return;
  const sections = window.prompt(
    t("js.prompt_sections") + "\n" + settings.schema.section_order.join(", "),
    settings.schema.section_order.join(",")
  );
  if (!sections) return;
  const wanted = sections.split(",").map((item) => item.trim()).filter(Boolean);
  const result = await call("copy_settings_from", settings.instanceId, source, wanted, settings.draft);
  if (!result || result.ok === false) {
    settingsLog(t("js.copy_detail", { detail: text(result && result.detail) }));
    return;
  }
  settings.draft = result.data;
  settingsLog(text(result.detail) + t("js.not_saved_yet"));
  renderForm();
  refreshSettingsDerived();
}

async function testQuickAction() {
  if (!settings.instanceId) return;
  const command = settings.draft.quick_action_command || settings.draft.on_double_click_command;
  const shown = Array.isArray(command) ? command.join(" ") : String(command || "");
  if (!shown) {
    settingsLog(t("js.no_quick_action"));
    return;
  }
  if (!window.confirm(t("js.confirm_quick_action") + "\n\n" + shown)) return;
  setBusy(true);
  const result = await call("test_quick_action", settings.instanceId, command);
  setBusy(false);
  if (!result) return;
  settingsLog("quick action: " + text(result.detail));
  if (result.stdout) settingsLog("stdout: " + result.stdout.trim());
  if (result.stderr) settingsLog("stderr: " + result.stderr.trim());
}

function wireSettings() {
  $("settings-instance").addEventListener("change", (event) => openSettings(event.target.value));
  document.querySelectorAll("#settings-tabs .tab").forEach((tab) => {
    tab.dataset.keepEnabled = "1";
    tab.addEventListener("click", () => showSubtab(tab.dataset.subtab));
  });
  $("settings-search").addEventListener("input", (event) => {
    settings.search = event.target.value;
    renderForm();
  });
  $("settings-json").addEventListener("change", (event) => {
    try {
      const parsed = JSON.parse(event.target.value || "{}");
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("il JSON deve essere un oggetto");
      }
      settings.draft = parsed;
      $("settings-json-errors").textContent = "";
      renderForm();
      refreshSettingsDerived();
    } catch (error) {
      $("settings-json-errors").textContent = String(error);
    }
  });
  $("btn-settings-save").addEventListener("click", () => saveSettings(false));
  $("btn-settings-save-restart").addEventListener("click", () => saveSettings(true));
  $("btn-settings-reload").addEventListener("click", () => openSettings(settings.instanceId));
  $("btn-settings-create").addEventListener("click", async () => {
    const result = await call("create_settings", settings.instanceId);
    if (!result || result.ok === false) {
      settingsLog("creazione: " + text(result && result.detail));
      return;
    }
    applySettingsPayload(result);
    settingsLog(text(result.detail));
  });
  $("btn-settings-folder").addEventListener("click", () => {
    if (settings.payload) call("open_folder", settings.payload.path);
  });
  $("btn-settings-independent").addEventListener("click", async () => {
    if (!settings.instanceId || !settings.payload.can_create_independent) return;
    setBusy(true);
    try {
      const result = await call("create_independent_settings", settings.instanceId, settings.draft);
      if (!result || result.ok === false) {
        toast(text(result && result.detail), "error");
        settingsLog(text(result && result.detail));
        if (result && result.validation) applyValidation(result.validation);
        return;
      }
      applySettingsPayload(result);
      settingsLog(text(result.detail));
      toast(text(result.detail), "ok");
      await refresh();
    } finally {
      setBusy(false);
    }
  });
  $("btn-settings-copy").addEventListener("click", copyFromInstance);
  $("btn-settings-export").addEventListener("click", async () => {
    const result = await call("export_settings", settings.instanceId);
    settingsLog("esportazione: " + text(result && result.detail));
  });
  $("btn-settings-import").addEventListener("click", async () => {
    const result = await call("import_settings", settings.instanceId);
    if (!result) return;
    settingsLog("importazione: " + text(result.detail));
    if (result.ok) applySettingsPayload(result);
  });
  $("btn-settings-preset").addEventListener("click", () => applyPreset($("settings-preset").value));
  $("btn-settings-quick").addEventListener("click", testQuickAction);
  $("btn-history-restore").addEventListener("click", restoreVersion);
}

/* ------------------------------------------------------ event plumbing */

function onEvent(payload) {
  if (!payload || !payload.type) return;
  if (payload.type === "update_event") {
    const event = payload.event || {};
    log("update-log", event.step + ": " + event.message);
    if (typeof event.progress === "number") {
      $("progress").hidden = false;
      $("progress-fill").style.width = Math.round(event.progress * 100) + "%";
    }
  } else if (payload.type === "process_event") {
    const event = payload.event || {};
    log("activity-log", event.action + " " + event.instance_id + ": " + event.message);
  } else if (payload.type === "update_state") {
    state.updates = payload.state || state.updates;
    renderUpdates();
    renderInstances();
  } else if (payload.type === "self_update_event") {
    const event = payload.event || {};
    log("self-update-log", event.step + ": " + event.message);
    if (typeof event.progress === "number") {
      $("self-progress").hidden = false;
      $("self-progress-fill").style.width = Math.round(event.progress * 100) + "%";
    }
  } else if (payload.type === "self_update_state") {
    state.updates.self_update = payload.state;
    if (state.manager) state.manager.self_update = payload.state;
    renderSelfUpdate(payload.state);
    renderUpdates();
  } else if (payload.type === "watchdog") {
    state.watchdog = payload.state || state.watchdog;
    renderInstances();
  }
}

window.umm = { goto, onEvent, refresh, openSettings, loadDiag, loadManager };

/* ------------------------------------------------------------- wiring */

function wire() {
  document.querySelectorAll(".nav-item[data-page]").forEach((item) => {
    item.dataset.keepEnabled = "1";
    item.addEventListener("click", () => goto(item.dataset.page));
  });
  wireChrome();
  document.querySelectorAll("[data-bulk]").forEach((button) => {
    button.addEventListener("click", () => bulk(button.dataset.bulk));
  });
  $("btn-refresh").addEventListener("click", refresh);
  $("btn-check").addEventListener("click", async () => {
    setBusy(true);
    log("activity-log", t("js.checking_updates"));
    const result = await call("check_updates");
    if (result) state.updates = result;
    setBusy(false);
    renderUpdates();
    renderInstances();
  });
  wireSettings();
  $("btn-apply").addEventListener("click", applyUpdate);
  $("btn-rollback").addEventListener("click", rollback);
  $("btn-skip").addEventListener("click", skipSelected);
  $("btn-token-set").addEventListener("click", setToken);
  $("btn-token-clear").addEventListener("click", clearToken);
  $("btn-rate-refresh").addEventListener("click", refreshRateLimit);
  $("btn-self-check").addEventListener("click", checkSelfUpdate);
  $("btn-self-apply").addEventListener("click", applySelfUpdate);
  $("btn-self-skip").addEventListener("click", skipSelfUpdate);
  $("btn-import-dry").addEventListener("click", () => runImport(true));
  $("btn-import").addEventListener("click", () => runImport(false));
  $("pref-autostart").addEventListener("change", async (event) => {
    const checked = event.target.checked;
    const result = await call("set_manager_autostart", checked);
    if (result && result.ok === false) {
      event.target.checked = !checked;
      toast(t("js.autostart_manager", { detail: text(result.detail) }), "error");
    } else {
      toast(t("js.autostart_manager", { detail: checked ? t("js.on") : t("js.off") }), "ok");
    }
  });
  $("pref-notify").addEventListener("change", (e) => setPreference("notify_updates", e.target.checked));
  $("pref-prerelease").addEventListener("change", (e) => setPreference("include_prerelease", e.target.checked));
  $("pref-interval").addEventListener("change", (e) => setPreference("check_interval_hours", Number(e.target.value)));
  $("pref-watchdog").addEventListener("change", (e) => setPreference("watchdog_enabled", e.target.checked));
  $("pref-auto-restart").addEventListener("change", (e) => setPreference("auto_restart_crashed", e.target.checked));
  $("pref-archive-keep").addEventListener("change", (e) => setPreference("archive_keep", Number(e.target.value)));

  $("btn-diag-start").addEventListener("click", startDiag);
  $("btn-diag-stop").addEventListener("click", stopDiag);
  $("btn-diag-report").addEventListener("click", copyDiagReport);
  $("btn-diag-refresh").addEventListener("click", refreshDiag);
  $("btn-diag-copy").addEventListener("click", () => copyText($("diag-log").textContent, t("js.log_copied")));
  $("diag-follow").addEventListener("change", toggleFollow);
  $("diag-source").addEventListener("change", (e) => {
    state.diag.source = e.target.value;
    refreshDiag();
  });
  $("btn-manager-log-refresh").addEventListener("click", refreshManagerLog);
  $("btn-kind-add").addEventListener("click", addKind);
  $("btn-profile-save").addEventListener("click", saveProfile);
  $("btn-hotkey-capture").addEventListener("click", captureHotkey);
  $("btn-hotkey-clear").addEventListener("click", () => {
    $("pref-hotkey").value = "";
    saveHotkey("");
  });
  $("pref-hotkey").addEventListener("change", (e) => saveHotkey(e.target.value));
  $("btn-status-file-save").addEventListener("click", () => saveStatusFile($("pref-status-file").value));
  $("btn-status-file-clear").addEventListener("click", () => {
    $("pref-status-file").value = "";
    saveStatusFile("");
  });

  const hash = (window.location.hash || "").replace("#", "");
  goto(hash || "dashboard");
}

/* The bridge (window.pywebview.api) is injected asynchronously and the
 * `pywebviewready` event can fire either before or after this script runs.
 * Wiring the DOM needs no bridge and happens at once; loading the data waits
 * for the bridge, polling as a fallback for the case where the event was
 * missed. Without this the first refresh() runs against a missing bridge and
 * the dashboard stays empty until the user clicks something. */
function whenBridgeReady(callback) {
  let done = false;
  const fire = () => {
    if (done) return;
    done = true;
    callback();
  };
  if (api()) {
    fire();
    return;
  }
  window.addEventListener("pywebviewready", fire);
  let attempts = 0;
  const timer = setInterval(() => {
    attempts += 1;
    if (api() || attempts > 100) {
      clearInterval(timer);
      fire();
    }
  }, 100);
}

function boot() {
  applyTheme(loadTheme());
  renderIcons();
  wire();
  whenBridgeReady(() => {
    loadTranslations();
    loadManager();
    loadPresets();
    refresh();
  });
}

if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

/* ------------------------------------------------------------ chrome
 *
 * Everything that is about the window itself rather than about the data:
 * inline icons, toasts, the activity drawer, the theme, the keyboard
 * shortcuts and the dashboard cards. Nothing here talks to the bridge.
 */

/* Minimal 24x24 stroke icons (Lucide-like), inlined so the page works
 * offline and without a font. */
const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/>',
  sliders: '<path d="M4 6h10"/><path d="M18 6h2"/><path d="M4 12h2"/><path d="M10 12h10"/><path d="M4 18h12"/><path d="M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  cog: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  activity: '<path d="M4 12h3l2-6 4 12 2-6h5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.9 4.9 1.4 1.4"/><path d="m17.7 17.7 1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.3 17.7-1.4 1.4"/><path d="m19.1 4.9-1.4 1.4"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>',
  play: '<path d="m6 4 14 8-14 8z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  rotate: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  check: '<path d="m5 12 5 5L20 7"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4"/><path d="M12 17h0"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h0"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  cards: '<rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="4" width="8" height="7" rx="1.5"/><rect x="3" y="13" width="8" height="7" rx="1.5"/><rect x="13" y="13" width="8" height="7" rx="1.5"/>',
  rows: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9"/><path d="m17 6 2 2"/><path d="m14 9 2 2"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h0"/><path d="M10 10h0"/><path d="M14 10h0"/><path d="M18 10h0"/><path d="M8 14h8"/>',
  box: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5"/><path d="M12 12v9"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  import: '<path d="M12 3v11"/><path d="m8 10 4 4 4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 9.8a6 6 0 0 1-7.9 7.9L7 21l-3-3 5.3-6.1A6 6 0 0 1 14.2 3z"/>',
};

function iconSvg(name) {
  const body = ICONS[name] || ICONS.info;
  return '<svg viewBox="0 0 24 24" aria-hidden="true">' + body + "</svg>";
}

function renderIcons(root) {
  (root || document).querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = iconSvg(el.dataset.icon);
  });
}

function iconEl(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = iconSvg(name);
  return span;
}

/* ---- toasts ---- */

const TOAST_ICONS = { ok: "check", warn: "alert", error: "alert", info: "info" };

function toast(message, level) {
  const kind = level || "info";
  const host = $("toasts");
  if (!host) return;
  const box = document.createElement("div");
  box.className = "toast " + kind;
  box.appendChild(iconEl(TOAST_ICONS[kind] || "info"));
  const body = document.createElement("span");
  body.textContent = message;
  box.appendChild(body);
  host.appendChild(box);
  while (host.children.length > 4) host.removeChild(host.firstChild);
  const close = () => {
    box.classList.add("leaving");
    setTimeout(() => box.remove(), 220);
  };
  box.addEventListener("click", close);
  setTimeout(close, kind === "error" ? 8000 : 4500);
  log("activity-log", message);
  if ($("activity-drawer").hidden) {
    const badge = $("activity-badge");
    badge.hidden = false;
    badge.textContent = String((Number(badge.textContent) || 0) + 1);
  }
}

/* One toast per process result: the detail with the right colour. */
function reportResults(results) {
  (results || []).forEach((result) => {
    const detail = text(result.detail, JSON.stringify(result));
    toast(detail, result.ok === false ? "error" : "ok");
  });
}

/* Update / rollback / restore: the log keeps the OK/ERRORE line, the toast
 * shows the same thing without having to open the log. */
function reportOutcome(logId, result) {
  const detail = text(result.detail);
  log(logId, (result.ok ? "OK: " : "ERRORE: ") + detail);
  toast(detail, result.ok ? "ok" : "error");
}

/* ---- activity drawer ---- */

function toggleActivity(force) {
  const drawer = $("activity-drawer");
  const show = force === undefined ? drawer.hidden : force;
  drawer.hidden = !show;
  $("btn-activity").classList.toggle("active", show);
  if (show) {
    const badge = $("activity-badge");
    badge.hidden = true;
    badge.textContent = "";
    const box = $("activity-log");
    box.scrollTop = box.scrollHeight;
  }
}

/* ---- theme ---- */

function loadTheme() {
  try {
    return localStorage.getItem("umm-theme") || "dark";
  } catch (error) {
    return "dark";
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const icon = document.querySelector("#btn-theme .nav-icon");
  if (icon) icon.innerHTML = iconSvg(theme === "light" ? "moon" : "sun");
  try {
    localStorage.setItem("umm-theme", theme);
  } catch (error) {
    /* private mode or no storage: the choice lasts for the session */
  }
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
}

/* ---- view ---- */

function loadView() {
  try {
    return localStorage.getItem("umm-view") === "table" ? "table" : "cards";
  } catch (error) {
    return "cards";
  }
}

function setView(view) {
  state.view = view === "table" ? "table" : "cards";
  try {
    localStorage.setItem("umm-view", state.view);
  } catch (error) {
    /* see applyTheme */
  }
  renderInstances();
}

/* ---- dashboard: stats and cards ---- */

function renderStats() {
  const running = state.rows.filter((row) => row.is_running).length;
  const updates = (state.updates.available || []).length;
  const alerts = state.rows.filter((row) => row.watchdog).length;
  const set = (id, value, tone) => {
    const node = $(id);
    node.textContent = String(value);
    node.parentElement.dataset.tone = tone;
  };
  set("stat-running", running + " / " + state.rows.length, running === state.rows.length && running ? "ok" : "");
  set("stat-updates", updates, updates ? "warn" : "");
  set("stat-alerts", alerts, alerts ? "warn" : "");
  set("stat-last-check", formatDate(state.updates.last_check), "");
  $("dashboard-hint").textContent = t("js.dashboard_hint", {
    running: running,
    total: state.rows.length,
    last: formatDate(state.updates.last_check),
  });
}

function autostartToggle(row) {
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.className = "switch";
  toggle.checked = Boolean(row.autostart_entry);
  toggle.title = text(row.autostart_command, t("js.no_run_entry"));
  toggle.disabled = !row.exe_path;
  toggle.addEventListener("change", async () => {
    setBusy(true);
    const result = await call("set_instance_autostart", row.id, toggle.checked);
    if (result && result.ok === false) {
      toast(t("js.autostart_of", { instance: row.id, detail: text(result.detail) }), "error");
      toggle.checked = !toggle.checked;
    } else {
      toast(
        t("js.autostart_of", {
          instance: row.id,
          detail: toggle.checked ? t("js.enabled") : t("js.disabled_f"),
        }),
        "ok"
      );
    }
    setBusy(false);
    refresh();
  });
  return toggle;
}

function iconButton(icon, label, handler, options) {
  const opts = options || {};
  const button = document.createElement("button");
  button.className =
    "btn small" + (opts.primary ? " primary" : "") + (opts.danger ? " danger" : "") + (opts.iconOnly ? " icon-only" : "");
  button.appendChild(iconEl(icon));
  if (opts.iconOnly) button.title = label;
  else button.appendChild(document.createTextNode(label));
  button.disabled = opts.enabled === false;
  button.addEventListener("click", handler);
  return button;
}

function metaRow(dl, label, value, options) {
  const opts = options || {};
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dd.title = value;
  if (opts.mono) dd.className = "mono";
  if (opts.path && value !== "—") {
    dd.classList.add("path");
    dd.title = value + "\n" + t("js.click_to_copy");
    dd.addEventListener("click", () => copyText(value, t("js.path_copied")));
  }
  dl.appendChild(dt);
  dl.appendChild(dd);
}

function renderInstanceCards() {
  const host = $("instances-cards");
  host.textContent = "";
  if (!state.rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state card-empty";
    const icon = document.createElement("span");
    icon.className = "empty-icon";
    icon.innerHTML = iconSvg("grid");
    const label = document.createElement("p");
    label.textContent = t("js.no_instances");
    empty.appendChild(icon);
    empty.appendChild(label);
    host.appendChild(empty);
    return;
  }

  state.rows.forEach((row) => {
    const card = document.createElement("article");
    card.className = "card" + (row.is_running ? " running" : "") + (row.watchdog ? " alert" : "");

    const head = document.createElement("div");
    head.className = "card-head";
    const dot = document.createElement("span");
    dot.className = "dot " + (row.is_running ? "on" : "off");
    head.appendChild(dot);
    const title = document.createElement("span");
    title.className = "card-title";
    title.textContent = text(row.label, row.kind_display || row.kind);
    title.title = row.id;
    head.appendChild(title);
    const kind = document.createElement("span");
    kind.className = "card-kind";
    kind.textContent = text(row.kind_display, row.kind) + " · " + text(row.local_version, "?");
    head.appendChild(kind);

    const badges = document.createElement("span");
    badges.className = "card-badges";
    if (row.update_available) {
      const pill = document.createElement("span");
      pill.className = "pill warn";
      pill.textContent = "↑ " + text(row.remote_version);
      badges.appendChild(pill);
    }
    if (row.watchdog) {
      const pill = document.createElement("span");
      pill.className = "pill danger";
      pill.textContent = row.watchdog in WATCHDOG_LABELS ? t(WATCHDOG_LABELS[row.watchdog]) : row.watchdog;
      pill.title = (watchdogEventsFor(row.id)[0] || {}).detail || "";
      badges.appendChild(pill);
    } else {
      const pill = document.createElement("span");
      pill.className = "pill " + (row.is_running ? "ok" : "");
      pill.textContent = row.is_running ? t("js.running") : t("js.stopped");
      badges.appendChild(pill);
    }
    head.appendChild(badges);
    card.appendChild(head);

    const meta = document.createElement("dl");
    meta.className = "card-meta";
    metaRow(meta, t("ui.col_config_dir"), text(row.config_dir), { path: true, mono: true });
    metaRow(meta, t("ui.col_active_settings"), text(row.settings_path), { path: true, mono: true });
    metaRow(
      meta,
      t("ui.col_pid") + " · " + t("ui.col_uptime"),
      row.is_running ? pidText(row) + " · " + formatUptime(row.uptime_seconds) : "—"
    );
    metaRow(meta, t("ui.col_exe_date"), formatDate(row.exe_mtime));
    card.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "card-actions";
    if (row.is_running) {
      actions.appendChild(iconButton("rotate", t("ui.restart_one"), () => act("restart", row.id)));
      actions.appendChild(iconButton("stop", t("ui.stop_one"), () => act("stop", row.id), { danger: true }));
    } else {
      actions.appendChild(
        iconButton("play", t("ui.start_one"), () => act("start", row.id), { enabled: Boolean(row.exe_path) })
      );
    }
    if (row.watchdog) {
      actions.appendChild(iconButton("wrench", t("ui.repair"), () => repairInstance(row.id)));
    }
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    actions.appendChild(spacer);

    const auto = document.createElement("label");
    auto.className = "row switch-row";
    auto.title = text(row.autostart_command, t("js.no_run_entry"));
    auto.appendChild(autostartToggle(row));
    const autoLabel = document.createElement("span");
    autoLabel.className = "hint";
    autoLabel.textContent = t("ui.col_autostart");
    auto.appendChild(autoLabel);
    actions.appendChild(auto);

    actions.appendChild(iconButton("sliders", t("ui.tab_settings"), () => showSettings(row.id), { iconOnly: true }));
    actions.appendChild(
      iconButton("folder", t("ui.folder"), () => call("open_folder", row.config_dir), { iconOnly: true })
    );
    if (row.update_available) {
      actions.appendChild(
        iconButton(
          "download",
          t("ui.update"),
          () => {
            selectUpdate(row.exe_path);
            goto("updates");
          },
          { primary: true }
        )
      );
    }
    card.appendChild(actions);
    host.appendChild(card);
  });
}

/* ---- shortcuts, drawer, theme, menus ---- */

const PAGE_KEYS = ["dashboard", "updates", "settings", "diag", "manager"];

function wireChrome() {
  state.view = loadView();
  document.querySelectorAll("#view-switch button").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.querySelectorAll(".stat[data-stat]").forEach((tile) => {
    tile.addEventListener("click", () => {
      if (tile.dataset.stat === "updates") goto("updates");
      else if (tile.dataset.stat === "alerts") toggleActivity(true);
      else refresh();
    });
  });
  $("btn-activity").addEventListener("click", () => toggleActivity());
  $("btn-activity-close").addEventListener("click", () => toggleActivity(false));
  $("btn-activity-clear").addEventListener("click", () => {
    $("activity-log").textContent = "";
  });
  $("btn-theme").addEventListener("click", toggleTheme);

  /* a menu closes after any of its items, and when clicking elsewhere */
  document.querySelectorAll("details.menu").forEach((menu) => {
    menu.querySelectorAll(".menu-item").forEach((item) => {
      item.addEventListener("click", () => menu.removeAttribute("open"));
    });
  });
  document.addEventListener("click", (event) => {
    document.querySelectorAll("details.menu[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });

  document.addEventListener("keydown", (event) => {
    const editing = /^(INPUT|TEXTAREA|SELECT)$/.test((event.target && event.target.tagName) || "");
    if (event.key === "Escape") {
      if (!$("activity-drawer").hidden) toggleActivity(false);
      document.querySelectorAll("details.menu[open]").forEach((menu) => menu.removeAttribute("open"));
      return;
    }
    if (event.key === "F5") {
      event.preventDefault();
      refresh();
      return;
    }
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const digit = Number(event.key);
    if (digit >= 1 && digit <= PAGE_KEYS.length) {
      event.preventDefault();
      goto(PAGE_KEYS[digit - 1]);
    } else if (event.key.toLowerCase() === "u" && !editing) {
      event.preventDefault();
      $("btn-check").click();
    } else if (event.key.toLowerCase() === "s" && state.page === "settings") {
      event.preventDefault();
      if (!$("btn-settings-save").disabled) saveSettings(false);
    }
  });
}

/* ---------------------------------------------------- markdown (tiny) */

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderInline(line) {
  let out = escapeHtml(line);
  out = out.replace(/`([^`]+)`/g, (_m, code) => "<code>" + code + "</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  out = out.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>');
  return out;
}

/* Supports what the monitors' release notes actually use: ATX headings,
 * unordered and ordered lists, fenced code blocks, inline code, bold, italic
 * and links. Anything else falls through as a paragraph. */
function renderMarkdown(source) {
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let listTag = null;
  let inCode = false;
  let paragraph = [];

  const closeParagraph = () => {
    if (paragraph.length) {
      out.push("<p>" + paragraph.map(renderInline).join("<br>") + "</p>");
      paragraph = [];
    }
  };
  const closeList = () => {
    if (listTag) {
      out.push("</" + listTag + ">");
      listTag = null;
    }
  };

  lines.forEach((raw) => {
    const line = raw.replace(/\s+$/, "");
    if (/^```/.test(line)) {
      closeParagraph();
      closeList();
      out.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      return;
    }
    if (inCode) {
      out.push(escapeHtml(raw) + "\n");
      return;
    }
    if (!line.trim()) {
      closeParagraph();
      closeList();
      return;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      closeParagraph();
      closeList();
      const level = Math.min(3, heading[1].length);
      out.push("<h" + level + ">" + renderInline(heading[2]) + "</h" + level + ">");
      return;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (bullet) {
      closeParagraph();
      if (listTag !== "ul") {
        closeList();
        listTag = "ul";
        out.push("<ul>");
      }
      out.push("<li>" + renderInline(bullet[1]) + "</li>");
      return;
    }
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ordered) {
      closeParagraph();
      if (listTag !== "ol") {
        closeList();
        listTag = "ol";
        out.push("<ol>");
      }
      out.push("<li>" + renderInline(ordered[1]) + "</li>");
      return;
    }
    closeList();
    paragraph.push(line);
  });

  if (inCode) out.push("</code></pre>");
  closeParagraph();
  closeList();
  return out.join("");
}

window.renderMarkdown = renderMarkdown;
