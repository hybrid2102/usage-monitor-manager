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

function goto(page) {
  if (!["dashboard", "updates", "manager"].includes(page)) page = "dashboard";
  state.page = page;
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.id === "page-" + page);
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.page === page);
  });
  if (page === "manager") loadManager();
  if (page === "updates") renderUpdates();
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
    td.colSpan = 11;
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

async function showSettings(id) {
  const data = await call("get_settings_raw", id);
  if (!data || data.ok === false) return;
  $("settings-panel").hidden = false;
  $("settings-title").textContent = "Impostazioni — " + text(data.label, id);
  $("settings-path").textContent = text(data.path, "nessun file di impostazioni trovato");
  $("settings-shadow").textContent = data.shadowed && data.shadowed.length
    ? "File ignorati (ombra): " + data.shadowed.join(", ")
    : "";
  $("settings-raw").textContent = data.text !== null && data.text !== undefined
    ? data.text
    : text(data.error, "(nessun contenuto)");
}

async function refresh() {
  const rows = await call("list_instances", true);
  if (rows) state.rows = rows;
  const updates = await call("get_update_state");
  if (updates) state.updates = updates;
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
  $("btn-apply").disabled = state.busy || !outdated;
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
    $("github-token").textContent = info.github_token_present
      ? "Token GitHub: presente"
      : "Token GitHub: assente (richieste anonime)";
    const rate = info.rate_limit;
    $("github-rate").textContent = rate
      ? "rate limit: " + text(rate.remaining) + "/" + text(rate.limit) + " · reset " + formatDate(rate.reset_at)
      : "rate limit: nessuna chiamata in questa sessione";
  }
  const config = await call("get_config");
  if (config && config.preferences) {
    $("pref-notify").checked = Boolean(config.preferences.notify_updates);
    $("pref-prerelease").checked = Boolean(config.preferences.include_prerelease);
    $("pref-interval").value = config.preferences.check_interval_hours;
  }
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
  }
}

window.umm = { goto, onEvent, refresh };

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
  $("settings-close").addEventListener("click", () => {
    $("settings-panel").hidden = true;
  });
  $("btn-apply").addEventListener("click", applyUpdate);
  $("btn-rollback").addEventListener("click", rollback);
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

  const hash = (window.location.hash || "").replace("#", "");
  goto(hash || "dashboard");
}

function boot() {
  wire();
  loadManager();
  refresh();
}

if (window.pywebview) {
  window.addEventListener("pywebviewready", boot);
  // pywebviewready may already have fired before this script ran
  if (window.pywebview.api) setTimeout(boot, 0);
} else {
  window.addEventListener("load", boot);
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
