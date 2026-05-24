const state = {
  config: null,
  overview: null,
  sessions: [],
  selectedSessionId: null,
  sessionDetail: null,
  sessionDetailLoading: false,
  sessionDetailCache: new Map(),
  events: [],
  logs: { lines: [], next: { logOffset: 0 } },
  health: null,
  filters: { status: "all", harness: "all", search: "" },
  tab: "logs",
  expandedLogSections: new Set(),
  expandedLogEntries: new Set(),
  freshness: null,
  shellTimer: null,
  sessionTimer: null,
};

const routes = [
  { id: "runs", label: "Runs", icon: "⌂", path: "/runs" },
  { id: "system", label: "System", icon: "⚙", path: "/system" },
];

window.addEventListener("popstate", routeChanged);
document.addEventListener("DOMContentLoaded", start);

async function start() {
  state.config = await api("/api/config");
  syncRouteSelection();
  await refresh();
  render();
  await loadSelectedSessionDetail();
  state.shellTimer = window.setInterval(refreshVisibleRoute, 3000);
  state.sessionTimer = window.setInterval(refreshLiveSession, 1000);
}

async function refreshVisibleRoute() {
  await refresh();
  render();
}

async function refreshLiveSession() {
  const route = currentRoute();
  if (route.name === "runs" && state.selectedSessionId) {
    await loadSelectedSessionDetail({ force: true, renderAfter: false });
    render();
  }
}

async function refresh() {
  const route = currentRoute();
  try {
    if (!state.config) state.config = await api("/api/config");
    if (route.name === "runs") {
      const [overview, sessions] = await Promise.all([
        api("/api/overview"),
        api(`/api/sessions?${sessionQuery()}`),
      ]);
      state.overview = overview;
      state.sessions = sessions.items;
    } else if (route.name === "system") {
      const [config, health] = await Promise.all([api("/api/config"), api("/api/health")]);
      state.config = config;
      state.health = health;
    }
    state.freshness = new Date();
  } catch (error) {
    state.error = error.message;
  }
}

function currentRoute() {
  const path = window.location.pathname === "/" ? "/runs" : window.location.pathname;
  if (path.startsWith("/sessions/")) {
    return { name: "runs", sessionId: decodeURIComponent(path.replace("/sessions/", "")) };
  }
  if (path === "/runs") {
    return { name: "runs", sessionId: new URLSearchParams(window.location.search).get("session") };
  }
  if (path === "/system") return { name: "system" };
  if (path === "/health" || path === "/settings" || path === "/events") return { name: "system" };
  return { name: "runs" };
}

function syncRouteSelection() {
  const route = currentRoute();
  const nextSessionId = route.name === "runs" ? route.sessionId : null;
  if (state.selectedSessionId !== nextSessionId) {
    state.expandedLogSections.clear();
    state.expandedLogEntries.clear();
  }
  state.selectedSessionId = nextSessionId;
  state.sessionDetail = state.selectedSessionId ? state.sessionDetailCache.get(state.selectedSessionId) || null : null;
  state.sessionDetailLoading = false;
}

function render() {
  const route = currentRoute();
  document.querySelector("#app").innerHTML = `
    <div class="app">
      ${rail(route)}
      <main class="main">
        <div class="topbar">
          <div class="topbar-left">
            <span class="product-name">Innie Dash</span>
            <span class="workspace">${escapeHtml(state.config?.workspace || "workspace loading")}</span>
          </div>
          <div class="topbar-right">
            <span class="utility-pill">read-only</span>
            <span class="freshness">${state.freshness ? `updated ${timeAgo(state.freshness)}` : "loading"}</span>
          </div>
        </div>
        <section class="content">${page(route)}</section>
      </main>
    </div>
  `;
  bind();
}

function rail(route) {
  const nav = routes
    .map((item) => {
      const active = route.name === item.id;
      return `<button class="nav-button ${active ? "active" : ""}" title="${item.label}" aria-label="${item.label}" data-route="${item.path}">${item.icon}</button>`;
    })
    .join("");
  return `<nav class="rail"><div class="mark" title="Innie Dash"><img class="mark-icon" src="/assets/innie-app-icon.svg" alt="Innie"></div>${nav}</nav>`;
}

function page(route) {
  if (state.error) {
    const message = state.error;
    state.error = null;
    return `<div class="error">${escapeHtml(message)}</div>`;
  }
  if (route.name === "system") return systemPage();
  return runsPage();
}

function runsPage() {
  const counts = state.overview?.counts || {};
  return `
    <h1 class="page-title">Runs</h1>
    <p class="subtitle">Recent Innie sessions from the local durable store.</p>
    <div class="metrics">
      ${metric("Sessions", counts.sessions)}
      ${metric("Running", counts.running_sessions)}
      ${metric("Queued", counts.queued_inputs)}
      ${metric("Failed", counts.failed_tasks)}
      ${metric("Locked", counts.locked_sessions)}
    </div>
    <div class="toolbar">
      <div class="segmented" aria-label="Status filter">
        ${segment("status", "all", "All", state.filters.status)}
        ${segment("status", "running", "Running", state.filters.status)}
        ${segment("status", "completed", "Completed", state.filters.status)}
        ${segment("status", "failed", "Failed", state.filters.status)}
      </div>
      <div class="filter-chip">
        <span>Harness</span>
        <select data-filter="harness">
          ${option("all", "Any", state.filters.harness)}
          ${option("codex", "Codex", state.filters.harness)}
          ${option("claude", "Claude", state.filters.harness)}
          ${option("echo", "Echo", state.filters.harness)}
        </select>
      </div>
      <input class="search" data-filter="search" placeholder="Search sessions, tasks, Slack text" value="${escapeAttr(state.filters.search)}">
    </div>
    <div class="runs-layout">
      <aside class="session-switcher runs-switcher">
        <div class="switcher-head">
          <span>Sessions</span>
          <button class="mini-button" data-refresh-shell>Refresh</button>
        </div>
        <div class="switcher-list">
          ${(state.sessions || []).map((row) => sessionSwitchRow(row, state.selectedSessionId)).join("") || `<div class="empty compact">No sessions found.</div>`}
        </div>
      </aside>
      ${sessionDetailPanel()}
    </div>
  `;
}

function sessionDetailPanel() {
  if (!state.selectedSessionId) {
    return `
      <section class="session-main">
        <div class="panel detail-placeholder">
          <div class="placeholder-copy">
            <strong>Select a session</strong>
            <span>Session details load on demand so this page stays quick while new sessions arrive.</span>
          </div>
        </div>
      </section>
    `;
  }
  if (state.sessionDetailLoading || !state.sessionDetail) {
    return sessionLoadingPanel();
  }
  const detail = state.sessionDetail;
  const session = detail.session;
  return `
    <section class="session-main">
      <div class="detail-header">
        <div>
          <h1 class="page-title">${escapeHtml(shortId(session.id))}</h1>
          <p class="subtitle">${escapeHtml(latestPrompt(detail) || "Durable session debugger")}</p>
        </div>
        <div class="session-actions">
          ${slackThreadLink(session)}
          ${chip(session.status)}
        </div>
      </div>
      <div class="meta-grid">
        ${meta("Harness", session.harness_id || "none")}
        ${meta("Output", session.output_target || "none")}
        ${meta("Slack thread", session.slack_thread_ts || session.slack_root_ts || "none")}
        ${meta("Lock", session.locked_by ? `${session.locked_by} until ${session.lock_expires_at || "unknown"}` : "none")}
      </div>
      <div class="session-toolbar">
        <div class="tabs">
          ${["logs", "records", "raw"].map(tabButton).join("")}
        </div>
        <div class="log-controls">
          <button class="mini-button" data-refresh-session>Refresh</button>
        </div>
      </div>
      <div class="panel">${sessionTab(detail)}</div>
    </section>
  `;
}

function sessionLoadingPanel() {
  const summary = state.sessions.find((row) => row.id === state.selectedSessionId);
  return `
    <section class="session-main">
      <div class="panel detail-placeholder" aria-busy="true">
        <div class="placeholder-copy">
          <strong>${escapeHtml(summary ? shortId(summary.id) : "Loading session")}</strong>
          <span>${escapeHtml(summary?.latest_user_message || summary?.latest_task_goal || "Fetching session detail from the local store.")}</span>
        </div>
        <div class="placeholder-lines">
          <span></span>
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    </section>
  `;
}

function sessionSwitchRow(row, activeId) {
  const preview = row.latest_user_message || row.latest_task_goal || row.last_event_type || row.id;
  return `
    <button class="session-switch-row ${row.id === activeId ? "active" : ""}" data-session="${escapeAttr(row.id)}">
      <span class="switch-row-top">
        <span class="switch-id">${escapeHtml(shortId(row.id))}</span>
        ${chip(row.status)}
      </span>
      <span class="switch-preview">${escapeHtml(preview)}</span>
      <span class="switch-meta">${escapeHtml(row.harness_id || "none")} · ${formatTime(row.updated_at)}</span>
    </button>
  `;
}

function latestPrompt(detail) {
  const inbox = detail.inbox || [];
  return inbox.length ? inbox[inbox.length - 1].text : "";
}

function slackThreadLink(session) {
  const url = slackUrl(session);
  if (!url) return "";
  return `<a class="mini-button link" href="${escapeAttr(url)}" target="_blank" rel="noreferrer">Open Slack</a>`;
}

function slackUrl(session) {
  const channel = session.slack_channel_id;
  const rootTs = session.slack_thread_ts || session.slack_root_ts;
  if (!channel || !rootTs) return "";
  const messageId = rootTs.replace(".", "");
  return `https://netflix.slack.com/archives/${encodeURIComponent(channel)}/p${encodeURIComponent(messageId)}`;
}

function sessionTab(detail) {
  if (state.tab === "records") return recordsTab(detail);
  if (state.tab === "raw") return `<pre>${escapeHtml(JSON.stringify(detail, null, 2))}</pre>`;
  return logsView(detail);
}

function recordsTab(detail) {
  return `
    <div class="records-stack">
      <section>
        <h3>Tasks</h3>
        ${tableRows(detail.tasks, ["id", "status", "harness_id", "goal", "updated_at"])}
      </section>
      <section>
        <h3>Inbox</h3>
        ${tableRows(detail.inbox, ["id", "status", "sender_user_id", "text", "created_at"])}
      </section>
      <section>
        <h3>Hooks</h3>
        ${tableRows(detail.hook_events, ["id", "hook_name", "status", "duration_ms", "created_at"])}
      </section>
      <section>
        <h3>Artifacts</h3>
        ${tableRows(detail.artifacts, ["id", "kind", "path", "created_at"])}
      </section>
    </div>
  `;
}

function logsView(detail) {
  const items = sessionLogItems(detail);
  const groups = groupSessionLogItems(items);
  return `
    <div class="timeline">${groups.map(logSectionCard).join("") || `<div class="empty">No log events.</div>`}</div>
  `;
}

function sessionLogItems(detail) {
  return [
    ...detail.inbox.map((row) => normalizeLogItem(row, `inbox.${row.status}`, "inbox")),
    ...detail.task_events.map((row) => normalizeLogItem(row, row.event_type, "event")),
    ...detail.hook_events.map((row) => normalizeLogItem(row, `hook.${row.hook_name}.${row.status}`, "hook")),
  ].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || String(b.id).localeCompare(String(a.id)));
}

function normalizeLogItem(row, eventType, source) {
  const payload = row.payload || safeJson(row.payload_json) || row;
  const itemId = `${source}:${row.id ?? row.created_at}:${eventType}`;
  return {
    ...row,
    id: itemId,
    source,
    event_type: eventType,
    payload,
    message: logMessage(payload, eventType),
  };
}

function groupSessionLogItems(items) {
  const chronological = [...items].sort(
    (a, b) => String(a.created_at).localeCompare(String(b.created_at)) || String(a.id).localeCompare(String(b.id)),
  );
  const groups = [];
  let current = null;

  chronological.forEach((item) => {
    if (item.event_type === "harness.progress") {
      if (current) groups.push(current);
      current = createProgressGroup(item);
      return;
    }
    if (!current) current = createActivityGroup(item);
    current.items.push(item);
    current.updated_at = maxTimestamp(current.updated_at, item.created_at);
  });

  if (current) groups.push(current);
  groups.forEach((group, index) => {
    group.step = group.progress ? index + 1 : null;
    group.items.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || String(b.id).localeCompare(String(a.id)));
  });
  return groups.reverse();
}

function createProgressGroup(item) {
  const summary = item.message || "Progress update";
  return {
    id: item.id,
    title: summary,
    summary,
    started_at: item.created_at,
    updated_at: item.created_at,
    progress: item,
    items: [],
  };
}

function createActivityGroup(item) {
  return {
    id: `activity:${item.id}`,
    title: "Session activity",
    summary: "Events before the first progress update.",
    started_at: item.created_at,
    updated_at: item.created_at,
    progress: null,
    items: [],
  };
}

function logSectionCard(group) {
  const count = group.items.length + (group.progress ? 1 : 0);
  const title = group.step ? `Step ${group.step}: ${group.title}` : group.title;
  const expanded = isLogSectionExpanded(group.id) ? " open" : "";
  const eventLabel = count === 1 ? "1 event" : `${count} events`;
  return `
    <details class="phase-card log-section" data-log-section="${escapeAttr(group.id)}"${expanded}>
      <summary class="log-section-head">
        <span class="section-chevron">›</span>
        <span class="log-section-copy">
          <span class="log-section-title">${escapeHtml(title)}</span>
          <span class="log-section-summary">${escapeHtml(group.summary)}</span>
        </span>
        <span class="log-section-meta">${escapeHtml(eventLabel)} · ${formatTime(group.updated_at)}</span>
      </summary>
      <div class="log-section-items">
        ${group.items.map(logEntryCard).join("") || `<div class="empty compact">No detailed events in this step.</div>`}
      </div>
    </details>
  `;
}

function logEntryCard(item) {
  const expanded = isLogEntryExpanded(item.id) ? " open" : "";
  const message = item.message || item.event_type || "Event";
  return `
    <div class="event log-entry">
      <span class="event-type">${escapeHtml(item.event_type || "event")}</span>
      <details data-log-entry="${escapeAttr(item.id)}"${expanded}>
        <summary class="log-entry-summary">
          <span class="entry-text">${escapeHtml(message)}</span>
          <span class="entry-action">more</span>
        </summary>
        <div class="event-message">${escapeHtml(message)}</div>
        <div class="event-head">
          <span>${escapeHtml(item.source)}</span>
          <span>${formatTime(item.created_at)}</span>
        </div>
        <pre class="expanded-payload">${escapeHtml(JSON.stringify(item.payload, null, 2))}</pre>
      </details>
    </div>
  `;
}

function isLogSectionExpanded(id) {
  return state.expandedLogSections.has(id);
}

function isLogEntryExpanded(id) {
  return state.expandedLogEntries.has(id);
}

function systemPage() {
  const config = state.config || {};
  const health = state.health;
  return `
    <h1 class="page-title">System</h1>
    <p class="subtitle">Local settings and metrics for this Innie workspace.</p>
    <div class="metrics">
      ${metric("Workspace", health?.workspace ? "1" : 0)}
      ${metric("Store", health?.store?.exists ? "up" : "missing")}
      ${metric("Log", health?.log?.exists ? "up" : "missing")}
      ${metric("DB bytes", health?.store?.sizeBytes)}
      ${metric("Log bytes", health?.log?.sizeBytes)}
    </div>
    <div class="panel">
      <div class="settings-list">
        ${health ? fileRow("Workspace", { path: health.workspace, exists: true }) : ""}
        ${health ? fileRow("Store", health.store) : ""}
        ${health ? fileRow("Log", health.log) : ""}
        ${setting("Workspace", config.workspace)}
        ${setting("Database", config.dbPath)}
        ${setting("Log", config.logPath)}
        ${setting("Refresh", `${config.refreshMs || 3000} ms`)}
      </div>
    </div>
  `;
}

function bind() {
  document.querySelectorAll("[data-route]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.route));
  });
  document.querySelectorAll("[data-session]").forEach((row) => {
    row.addEventListener("click", () => selectSession(row.dataset.session));
  });
  document.querySelectorAll("[data-filter]").forEach((control) => {
    control.addEventListener("change", updateFilter);
    control.addEventListener("input", debounce(updateFilter, 250));
  });
  document.querySelectorAll("[data-filter-button]").forEach((button) => {
    button.addEventListener("click", updateFilterButton);
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.tab = button.dataset.tab;
      render();
    });
  });
  document.querySelectorAll("[data-refresh-session]").forEach((button) => {
    button.addEventListener("click", async () => {
      await loadSelectedSessionDetail({ force: true });
    });
  });
  document.querySelectorAll("[data-refresh-shell]").forEach((button) => {
    button.addEventListener("click", async () => {
      await refresh();
      render();
    });
  });
  document.querySelectorAll("[data-log-section]").forEach((section) => {
    section.addEventListener("toggle", () => {
      setExpanded(state.expandedLogSections, section.dataset.logSection, section.open);
    });
  });
  document.querySelectorAll("[data-log-entry]").forEach((entry) => {
    entry.addEventListener("toggle", () => {
      setExpanded(state.expandedLogEntries, entry.dataset.logEntry, entry.open);
    });
  });
}

async function updateFilter(event) {
  const key = event.target.dataset.filter;
  state.filters[key] = event.target.value;
  const sessions = await api(`/api/sessions?${sessionQuery()}`);
  state.sessions = sessions.items;
  if (state.selectedSessionId && !state.sessions.some((row) => row.id === state.selectedSessionId)) {
    state.selectedSessionId = null;
    state.sessionDetail = null;
    window.history.replaceState({}, "", "/runs");
  }
  state.freshness = new Date();
  render();
}

async function updateFilterButton(event) {
  state.filters[event.currentTarget.dataset.filterButton] = event.currentTarget.dataset.value;
  const sessions = await api(`/api/sessions?${sessionQuery()}`);
  state.sessions = sessions.items;
  if (state.selectedSessionId && !state.sessions.some((row) => row.id === state.selectedSessionId)) {
    state.selectedSessionId = null;
    state.sessionDetail = null;
    window.history.replaceState({}, "", "/runs");
  }
  state.freshness = new Date();
  render();
}

async function selectSession(sessionId, { updateUrl = true } = {}) {
  if (!sessionId) return;
  if (state.selectedSessionId !== sessionId) {
    state.expandedLogSections.clear();
    state.expandedLogEntries.clear();
  }
  state.selectedSessionId = sessionId;
  state.tab = "logs";
  state.sessionDetail = state.sessionDetailCache.get(sessionId) || null;
  if (updateUrl) {
    window.history.pushState({}, "", `/runs?session=${encodeURIComponent(sessionId)}`);
  }
  render();
  await loadSelectedSessionDetail();
}

async function loadSelectedSessionDetail({ force = false, renderAfter = true } = {}) {
  const sessionId = state.selectedSessionId;
  if (!sessionId) return;
  if (!force && state.sessionDetailCache.has(sessionId)) {
    state.sessionDetail = state.sessionDetailCache.get(sessionId);
    if (renderAfter) render();
    return;
  }
  state.sessionDetailLoading = true;
  if (renderAfter) render();
  try {
    const detail = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (state.selectedSessionId !== sessionId) return;
    state.sessionDetailCache.set(sessionId, detail);
    state.sessionDetail = detail;
    state.freshness = new Date();
  } catch (error) {
    state.error = error.message;
  } finally {
    if (state.selectedSessionId === sessionId) state.sessionDetailLoading = false;
    if (renderAfter) render();
  }
}

function routeChanged() {
  syncRouteSelection();
  refresh().then(() => {
    render();
    return loadSelectedSessionDetail();
  });
}

function navigate(path) {
  window.history.pushState({}, "", path);
  syncRouteSelection();
  state.tab = "logs";
  state.logs = { lines: [], next: { logOffset: 0 } };
  refresh().then(() => {
    render();
    return loadSelectedSessionDetail();
  });
}

function sessionQuery() {
  const params = new URLSearchParams();
  Object.entries(state.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}

async function api(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

function metric(label, value) {
  return `<div class="metric"><span class="metric-label">${label}</span><span class="metric-value">${value ?? 0}</span></div>`;
}

function segment(filter, value, label, current) {
  return `<button class="segment ${value === current ? "active" : ""}" data-filter-button="${escapeAttr(filter)}" data-value="${escapeAttr(value)}">${escapeHtml(label)}</button>`;
}

function option(value, label, current) {
  return `<option value="${escapeAttr(value)}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</option>`;
}

function chip(status) {
  return `<span class="chip ${escapeAttr(status || "")}">${escapeHtml(status || "unknown")}</span>`;
}

function meta(label, value) {
  return `<div class="meta"><span>${escapeHtml(label)}</span><strong title="${escapeAttr(value)}">${escapeHtml(value)}</strong></div>`;
}

function tabButton(tab) {
  return `<button class="tab ${state.tab === tab ? "active" : ""}" data-tab="${tab}">${tab}</button>`;
}

function logMessage(payload, eventType) {
  const value = payload.message || payload.text || payload.goal || payload.error || "";
  if (value) return String(value);
  return eventType || "Event";
}

function maxTimestamp(left, right) {
  return String(right).localeCompare(String(left)) > 0 ? right : left;
}

function setExpanded(set, key, expanded) {
  if (!key) return;
  if (expanded) {
    set.add(key);
  } else {
    set.delete(key);
  }
}

function tableRows(rows, keys) {
  if (!rows || rows.length === 0) return `<div class="empty">No rows.</div>`;
  return `
    <table>
      <thead><tr>${keys.map((key) => `<th>${escapeHtml(key)}</th>`).join("")}</tr></thead>
      <tbody>
        ${rows
          .map((row) => `<tr>${keys.map((key) => `<td title="${escapeAttr(String(row[key] ?? ""))}">${escapeHtml(String(row[key] ?? ""))}</td>`).join("")}</tr>`)
          .join("")}
      </tbody>
    </table>
  `;
}

function fileRow(label, file) {
  return `
    <div class="setting-row">
      <strong>${escapeHtml(label)}</strong>
      <span>${file.exists ? "available" : "missing"} · ${escapeHtml(file.path || "")}${file.sizeBytes != null ? ` · ${file.sizeBytes} bytes` : ""}</span>
    </div>
  `;
}

function setting(label, value) {
  return `<div class="setting-row"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value || "unset")}</span></div>`;
}

function lastEventId() {
  return state.events.reduce((max, event) => Math.max(max, event.id || 0), 0);
}

function mergeById(existing, incoming) {
  const map = new Map(existing.map((item) => [item.id, item]));
  incoming.forEach((item) => map.set(item.id, item));
  return Array.from(map.values()).sort((a, b) => (a.id || 0) - (b.id || 0));
}

function shortId(id) {
  if (!id) return "none";
  return id.length > 22 ? `${id.slice(0, 10)}...${id.slice(-6)}` : id;
}

function formatTime(value) {
  if (!value) return "none";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function timeAgo(date) {
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  return `${seconds}s ago`;
}

function safeJson(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function debounce(fn, ms) {
  let timer;
  return (event) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(event), ms);
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}
