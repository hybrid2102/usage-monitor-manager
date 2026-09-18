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
};

const WATCHDOG_LABELS = {
  crashed: "chiusa da sola",
  duplicate: "processi doppi",
  orphan: "coppia incompleta",
};

/* ------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

async function call(name, ...args) {
  const bridge = api();
  if (!bridge || typeof bridge[name] !== "function") {
    log("manager-log", "bridge non disponibile: " + name);
    return null;
  }
  try {
    return await bridge[name](...args);
  } catch (error) {
    log("manager-log", name + " non riuscito: " + error);
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
  document.querySelectorAll("button").forEach((button) => {
    if (button.dataset.keepEnabled === "1") return;
    button.disabled = busy;
  });
}

/* ---------------------------------------------------------- navigation */

function goto(page, focus) {
  if (!["dashboard", "updates", "settings", "manager"].includes(page)) page = "dashboard";
  state.page = page;
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.id === "page-" + page);
  });
  document.querySelectorAll("header .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.page === page);
  });
  if (page === "manager") loadManager();
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

function renderInstances() {
  const body = $("instances-body");
  body.textContent = "";
  if (!state.rows.length) {
    const tr = document.createElement("tr");
    tr.className = "empty";
    const td = document.createElement("td");
    td.colSpan = 12;
    td.textContent = "Nessuna istanza trovata";
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
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = Boolean(row.autostart_entry);
    toggle.title = text(row.autostart_command, "nessuna voce in HKCU\\...\\Run");
    toggle.disabled = !row.exe_path;
    toggle.addEventListener("change", async () => {
      setBusy(true);
      const result = await call("set_instance_autostart", row.id, toggle.checked);
      if (result && result.ok === false) {
        log("manager-log", "autostart " + row.id + ": " + text(result.detail));
        toggle.checked = !toggle.checked;
      } else {
        log("manager-log", "autostart " + row.id + ": " + (toggle.checked ? "attivato" : "disattivato"));
      }
      setBusy(false);
      refresh();
    });
    autostart.appendChild(toggle);
    tr.appendChild(autostart);

    const health = document.createElement("td");
    if (row.watchdog) {
      const badge = document.createElement("span");
      badge.className = "badge-warn";
      badge.textContent = WATCHDOG_LABELS[row.watchdog] || row.watchdog;
      badge.title = (watchdogEventsFor(row.id)[0] || {}).detail || "";
      health.appendChild(badge);
    } else {
      health.textContent = row.is_running ? "ok" : "—";
    }
    tr.appendChild(health);

    const actions = document.createElement("td");
    actions.className = "actions-col";
    const buttons = [
      ["Riavvia", () => act("restart", row.id), row.is_running],
      ["Ferma", () => act("stop", row.id), row.is_running],
      ["Avvia", () => act("start", row.id), !row.is_running && Boolean(row.exe_path)],
      ["Impostazioni", () => showSettings(row.id), true],
      ["Cartella", () => call("open_folder", row.config_dir), true],
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
      repair.textContent = "Ripara";
      repair.addEventListener("click", () => repairInstance(row.id));
      actions.appendChild(repair);
    }
    if (row.update_available) {
      const button = document.createElement("button");
      button.className = "btn small primary";
      button.textContent = "Aggiorna";
      button.addEventListener("click", () => {
        selectUpdate(row.exe_path);
        goto("updates");
      });
      actions.appendChild(button);
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  });

  const running = state.rows.filter((row) => row.is_running).length;
  $("dashboard-hint").textContent =
    running + " di " + state.rows.length + " in esecuzione · ultimo controllo aggiornamenti: " +
    formatDate(state.updates.last_check);
}

function watchdogEventsFor(id) {
  return (state.watchdog.events || []).filter((event) => event.id === id);
}

async function repairInstance(id) {
  setBusy(true);
  log("manager-log", "riparazione di " + id + "…");
  const result = await call("repair_instance", id);
  if (result) log("manager-log", "  " + text(result.detail, JSON.stringify(result)));
  setBusy(false);
  await refresh();
}

async function act(action, id) {
  setBusy(true);
  log("manager-log", action + " " + id + "…");
  const results = await call(action, [id]);
  (results || []).forEach((result) => log("manager-log", "  " + text(result.detail, JSON.stringify(result))));
  setBusy(false);
  await refresh();
}

async function bulk(action) {
  setBusy(true);
  log("manager-log", action + "…");
  const results = await call(action);
  (results || []).forEach((result) => log("manager-log", "  " + text(result.detail, JSON.stringify(result))));
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
  const list = $("updates-list");
  list.textContent = "";
  const plans = planList();
  $("updates-status").textContent = state.updates.error
    ? "ultimo controllo non riuscito: " + state.updates.error
    : plans.length
      ? (state.updates.update_count || 0) + " aggiornamenti disponibili"
      : "nessun controllo eseguito";

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
    $("changelog").textContent = "Nessun changelog disponibile.";
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
  $("btn-skip").textContent = skipped ? "Non saltare piu'" : "Salta questa versione";
  renderArchive();
}

function renderArchive() {
  const host = $("archive-list");
  if (!host) return;
  host.textContent = "";
  if (!state.archived.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "Nessuna build archiviata per questo eseguibile.";
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
    button.textContent = "Ripristina";
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
  if (result) log("update-log", (result.ok ? "OK: " : "ERRORE: ") + text(result.detail));
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
  if (result) log("update-log", text(result.detail, JSON.stringify(result)));
  setBusy(false);
  await refresh();
  renderDetail();
}

async function applyUpdate() {
  if (!state.selected) return;
  setBusy(true);
  $("progress").hidden = false;
  $("progress-fill").style.width = "0%";
  log("update-log", "aggiornamento di " + state.selected);
  const result = await call("apply_update", state.selected);
  if (result) log("update-log", (result.ok ? "OK: " : "ERRORE: ") + text(result.detail));
  $("progress").hidden = true;
  setBusy(false);
  await refresh();
}

async function rollback() {
  if (!state.selected) return;
  setBusy(true);
  log("update-log", "rollback di " + state.selected);
  const result = await call("rollback", state.selected);
  if (result) log("update-log", (result.ok ? "OK: " : "ERRORE: ") + text(result.detail));
  setBusy(false);
  await refresh();
}

/* ------------------------------------------------------------- manager */

async function loadManager() {
  const info = await call("get_manager_info");
  if (info) {
    $("header-version").textContent = info.version;
    $("manager-version").textContent = "Usage Monitor Manager " + info.version;
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
  }
}

function renderToken(info) {
  const stored = info.github_token_stored;
  const source = info.github_token_source;
  $("github-token").textContent = info.github_token_present
    ? "Token GitHub: presente" + (stored ? " (Credential Manager)" : " (variabile " + source + ")")
    : "Token GitHub: assente (richieste anonime, 60/ora)";
  const rate = info.rate_limit;
  $("github-rate").textContent = rate
    ? "rate limit: " + text(rate.remaining) + "/" + text(rate.limit) + " · reset " + formatDate(rate.reset_at)
    : "rate limit: nessuna chiamata in questa sessione";
  $("github-target").textContent = "credenziale: " + text(info.credential_target);
  $("btn-token-clear").disabled = !stored;
}

function renderSelfUpdate(own) {
  const box = $("self-update-state");
  const version = state.manager ? state.manager.version : "";
  if (!own) {
    box.textContent = "Manager " + version + " · nessun controllo eseguito";
  } else {
    box.textContent = "Manager " + version + " · " + text(own.detail);
  }
  const available = Boolean(own && own.available);
  const frozen = Boolean(state.manager && state.manager.frozen);
  $("btn-self-apply").disabled = state.busy || !available || !frozen;
  $("btn-self-apply").title = frozen ? "" : "disponibile solo per UsageMonitorManager.exe";
  $("btn-self-skip").disabled = state.busy || !(own && own.tag);
  $("btn-self-skip").textContent = own && own.skipped ? "Non saltare piu'" : "Salta questa versione";
}

async function setToken() {
  const field = $("token-input");
  const value = field.value;
  if (!value.trim()) {
    log("manager-log", "token: campo vuoto, niente da salvare");
    return;
  }
  setBusy(true);
  const result = await call("set_github_token", value);
  field.value = ""; // the value is never shown again, here or anywhere else
  if (result) log("manager-log", text(result.detail));
  setBusy(false);
  await loadManager();
}

async function clearToken() {
  setBusy(true);
  const result = await call("clear_github_token");
  if (result) log("manager-log", text(result.detail));
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
  log("self-update-log", "aggiornamento del manager…");
  const result = await call("apply_self_update");
  if (result) log("self-update-log", (result.ok ? "OK: " : "ERRORE: ") + text(result.detail));
  $("self-progress").hidden = true;
  setBusy(false);
}

async function skipSelfUpdate() {
  const own = state.manager ? state.manager.self_update : null;
  const result = await call(own && own.skipped ? "unskip_version" : "skip_version", "manager");
  if (result) log("manager-log", text(result.detail, JSON.stringify(result)));
  await loadManager();
}

async function setPreference(key, value) {
  const result = await call("set_preference", key, value);
  log("manager-log", "preferenza " + key + " = " + value + (result && result.ok === false ? " NON salvata" : ""));
}

async function runImport(dryRun) {
  const report = await call("import_legacy", dryRun);
  const box = $("import-log");
  box.textContent = report ? (report.lines || []).join("\n") : "importazione non riuscita";
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
    ? errors.length + " valori non validi"
    : dirtyCount() + " modifiche non salvate";
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
  reset.textContent = "Reset al default";
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
        settingsLog(field.key + ": JSON non valido (" + error + ")");
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
        settingsLog(field.key + ": valore non interpretabile (" + error + ")");
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
      remove.textContent = "Rimuovi";
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
  add.textContent = "Aggiungi";
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
    empty.textContent = "Nessuna differenza.";
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
    empty.textContent = "Nessuna chiave sconosciuta.";
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
        settingsLog(key + ": JSON non valido (" + error + ")");
      }
    });
    const remove = document.createElement("button");
    remove.className = "btn small danger";
    remove.textContent = "Rimuovi";
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
    empty.textContent = "Nessuna versione archiviata.";
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
  $("settings-history-title").textContent = "Versione " + stamp;
  renderDiffInto($("settings-history-diff"), result.diff.map((line) => line));
}

async function restoreVersion() {
  if (!settings.instanceId || !settings.historyStamp) return;
  if (!window.confirm("Ripristinare la versione " + settings.historyStamp + "? Il file attuale viene archiviato.")) return;
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
  $("settings-file").textContent = payload.path + (payload.exists ? "" : "  (non esiste)");
  $("settings-shadow").textContent =
    payload.shadowed && payload.shadowed.length ? "File ignorati (ombra): " + payload.shadowed.join(", ") : "";
  $("btn-settings-create").disabled = Boolean(payload.exists);
  if (payload.error) settingsLog("lettura: " + payload.error);
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
  if (restart && !window.confirm("Salvare e riavviare l'istanza? Il monitor viene fermato e riavviato.")) return;
  setBusy(true);
  const result = await call("save_settings", settings.instanceId, settings.draft, Boolean(restart));
  setBusy(false);
  if (!result || result.ok === false) {
    settingsLog("salvataggio non riuscito: " + text(result && result.detail));
    if (result && result.validation) applyValidation(result.validation);
    return;
  }
  applySettingsPayload(result);
  settingsLog(text(result.detail));
  await refresh();
}

async function copyFromInstance() {
  if (!settings.instanceId || !settings.schema) return;
  const others = state.rows.filter(
    (row) => row.id !== settings.instanceId && row.kind === settings.schema.kind
  );
  if (!others.length) {
    settingsLog("nessun'altra istanza dello stesso tipo");
    return;
  }
  const source = window.prompt(
    "Copia da quale istanza?\n" + others.map((row) => row.id).join("\n"),
    others[0].id
  );
  if (!source) return;
  const sections = window.prompt(
    "Quali sezioni? (separate da virgola)\n" + settings.schema.section_order.join(", "),
    settings.schema.section_order.join(",")
  );
  if (!sections) return;
  const wanted = sections.split(",").map((item) => item.trim()).filter(Boolean);
  const result = await call("copy_settings_from", settings.instanceId, source, wanted, settings.draft);
  if (!result || result.ok === false) {
    settingsLog("copia: " + text(result && result.detail));
    return;
  }
  settings.draft = result.data;
  settingsLog(text(result.detail) + " (non ancora salvato)");
  renderForm();
  refreshSettingsDerived();
}

async function testQuickAction() {
  if (!settings.instanceId) return;
  const command = settings.draft.quick_action_command || settings.draft.on_double_click_command;
  const shown = Array.isArray(command) ? command.join(" ") : String(command || "");
  if (!shown) {
    settingsLog("nessuna quick action configurata");
    return;
  }
  if (!window.confirm("Eseguire davvero la quick action?\n\n" + shown)) return;
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
    log("manager-log", event.action + " " + event.instance_id + ": " + event.message);
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
    if (state.manager) state.manager.self_update = payload.state;
    renderSelfUpdate(payload.state);
  } else if (payload.type === "watchdog") {
    state.watchdog = payload.state || state.watchdog;
    renderInstances();
  }
}

window.umm = { goto, onEvent, refresh, openSettings };

/* ------------------------------------------------------------- wiring */

function wire() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.dataset.keepEnabled = "1";
    tab.addEventListener("click", () => goto(tab.dataset.page));
  });
  document.querySelectorAll("[data-bulk]").forEach((button) => {
    button.addEventListener("click", () => bulk(button.dataset.bulk));
  });
  $("btn-refresh").addEventListener("click", refresh);
  $("btn-check").addEventListener("click", async () => {
    setBusy(true);
    log("manager-log", "controllo aggiornamenti…");
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
      log("manager-log", "autostart manager: " + text(result.detail));
    } else {
      log("manager-log", "autostart manager: " + (checked ? "attivo" : "disattivo"));
    }
  });
  $("pref-notify").addEventListener("change", (e) => setPreference("notify_updates", e.target.checked));
  $("pref-prerelease").addEventListener("change", (e) => setPreference("include_prerelease", e.target.checked));
  $("pref-interval").addEventListener("change", (e) => setPreference("check_interval_hours", Number(e.target.value)));
  $("pref-watchdog").addEventListener("change", (e) => setPreference("watchdog_enabled", e.target.checked));
  $("pref-auto-restart").addEventListener("change", (e) => setPreference("auto_restart_crashed", e.target.checked));
  $("pref-archive-keep").addEventListener("change", (e) => setPreference("archive_keep", Number(e.target.value)));

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
  wire();
  whenBridgeReady(() => {
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
