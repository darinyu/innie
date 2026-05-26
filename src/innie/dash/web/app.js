const state = {
  theme: "light",
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
  expandedPhaseSections: new Set(),
  expandedLogEntries: new Set(),
  pendingSessionId: null,
  pendingToggleId: null,
  freshness: null,
  shellTimer: null,
  sessionTimer: null,
};

const THEME_STORAGE_KEY = "innie.dash.theme";

const routes = [
  { id: "runs", label: "Runs", icon: "⌂", path: "/runs" },
  { id: "system", label: "System", icon: "⚙", path: "/system" },
];

window.addEventListener("popstate", routeChanged);
document.addEventListener("DOMContentLoaded", start);

async function start() {
  initializeTheme();
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
    state.expandedPhaseSections.clear();
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
            ${themeToggle()}
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

function initializeTheme() {
  const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  state.theme = storedTheme === "light" || storedTheme === "dark" ? storedTheme : "light";
  document.documentElement.dataset.theme = state.theme;
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
      ${metric("Sessions", counts.sessions, "status", "all")}
      ${metric("Active", counts.running_sessions, "status", "running")}
      ${metric("Queued", counts.queued_sessions, "status", "queued")}
      ${metric("Failed", counts.failed_tasks, "status", "failed")}
    </div>
    <div class="toolbar">
      <div class="segmented" aria-label="Status filter">
        ${segment("status", "all", "All", state.filters.status)}
        ${segment("status", "running", "Running", state.filters.status)}
        ${segment("status", "queued", "Queued", state.filters.status)}
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
        ${meta("Worker", workerSummary(detail.worker))}
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
          <strong>${spinner("Loading")} ${escapeHtml(summary ? shortId(summary.id) : "Loading session")}</strong>
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
  const queue = Number(row.queued_inputs || 0);
  const queueLabel = queue ? ` · ${queue} queued` : "";
  const workerLabel = row.lock_state && row.lock_state !== "idle" ? ` · worker ${row.lock_state}` : "";
  const pending = state.pendingSessionId === row.id;
  return `
    <button class="session-switch-row ${row.id === activeId ? "active" : ""} ${pending ? "pending" : ""}" data-session="${escapeAttr(row.id)}" aria-busy="${pending ? "true" : "false"}">
      <span class="switch-row-top">
        <span class="switch-id">${escapeHtml(shortId(row.id))}</span>
        ${pending ? spinner("Loading session") : chip(sessionListStatus(row))}
      </span>
      <span class="switch-preview">${escapeHtml(preview)}</span>
      <span class="switch-meta">${escapeHtml(row.harness_id || "none")}${escapeHtml(queueLabel)}${escapeHtml(workerLabel)} · ${formatTime(row.updated_at)}</span>
    </button>
  `;
}

function sessionListStatus(row) {
  if (row.latest_task_status === "failed") return "failed";
  return row.status;
}

function latestPrompt(detail) {
  const inbox = detail.inbox || [];
  return inbox.length ? inbox[inbox.length - 1].text : "";
}

function workerSummary(worker) {
  if (!worker) return "none";
  const parts = [worker.status || "idle"];
  if (worker.lock_owner) parts.push(worker.lock_owner);
  if (worker.queue_depth) parts.push(`${worker.queue_depth} queued`);
  if (worker.current_task_id) parts.push(`task ${shortId(worker.current_task_id)}`);
  if (worker.latest_event?.event_type) parts.push(worker.latest_event.event_type);
  return parts.join(" · ");
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
  const params = new URLSearchParams();
  params.set("channel", channel);
  params.set("message_ts", rootTs);
  return `https://slack.com/app_redirect?${params.toString()}`;
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
  const groups = sessionTurnGroups(detail);
  return `
    <div class="timeline timeline-ledger">${groups.map(logSectionCard).join("") || `<div class="empty">No log events.</div>`}</div>
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
    row_id: row.id,
    source,
    event_type: eventType,
    payload,
    message: logMessage(payload, eventType),
  };
}

function sessionTurnGroups(detail) {
  const index = buildTurnIndex(detail);
  const activity = createActivityGroup();

  sessionLogItems(detail).forEach((item) => {
    const group = turnGroupForItem(item, index);
    if (group) {
      group.items.push(item);
      group.updated_at = maxTimestamp(group.updated_at, item.created_at);
    } else {
      activity.items.push(item);
      activity.updated_at = maxTimestamp(activity.updated_at, item.created_at);
    }
  });

  const groups = index.groups.map(finalizeTurnGroup);
  if (activity.items.length) groups.push(finalizeActivityGroup(activity));
  return groups
    .sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)) || String(a.id).localeCompare(String(b.id)))
    .reverse();
}

function buildTurnIndex(detail) {
  const groups = [];
  const byInboxId = new Map();
  const byTaskId = new Map();

  [...(detail.inbox || [])]
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || String(a.id).localeCompare(String(b.id)))
    .forEach((row, index) => {
      const group = createTurnGroup(row, index + 1);
      groups.push(group);
      byInboxId.set(String(row.id), group);
    });

  let activeGroup = null;
  [...(detail.task_events || [])]
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || String(a.id).localeCompare(String(b.id)))
    .forEach((row) => {
      const payload = row.payload || safeJson(row.payload_json) || {};
      const inboxId = payload.inbox_id == null ? null : String(payload.inbox_id);
      const taskId = row.task_id || payload.task_id;

      if (row.event_type === "worker.inbox.claimed" && inboxId && byInboxId.has(inboxId)) {
        activeGroup = byInboxId.get(inboxId);
      }
      if (inboxId && byInboxId.has(inboxId) && taskId) {
        connectTaskToGroup(taskId, byInboxId.get(inboxId), byTaskId);
      }
      if (taskId && activeGroup && !byTaskId.has(String(taskId))) {
        connectTaskToGroup(taskId, activeGroup, byTaskId);
      }
      if (row.event_type === "worker.session.released") {
        activeGroup = null;
      }
    });

  [...(detail.tasks || [])]
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || String(a.id).localeCompare(String(b.id)))
    .forEach((task, index) => {
      if (!byTaskId.has(String(task.id)) && groups[index]) {
        connectTaskToGroup(task.id, groups[index], byTaskId);
      }
    });

  return { groups, byInboxId, byTaskId };
}

function connectTaskToGroup(taskId, group, byTaskId) {
  if (!taskId || !group) return;
  const key = String(taskId);
  byTaskId.set(key, group);
  group.taskIds.add(key);
}

function turnGroupForItem(item, index) {
  if (item.source === "inbox" && item.row_id != null) {
    return index.byInboxId.get(String(item.row_id));
  }
  if (item.payload.inbox_id != null) {
    const group = index.byInboxId.get(String(item.payload.inbox_id));
    if (group) return group;
  }
  if (item.task_id) {
    const group = index.byTaskId.get(String(item.task_id));
    if (group) return group;
  }
  if (item.payload.task_id) {
    const group = index.byTaskId.get(String(item.payload.task_id));
    if (group) return group;
  }
  return null;
}

function createTurnGroup(row, turnNumber) {
  const prompt = row.text || "Slack reply";
  return {
    id: `turn:${row.id}`,
    title: `Reply ${turnNumber}`,
    summary: prompt,
    prompt,
    turnNumber,
    started_at: row.created_at,
    updated_at: row.processed_at || row.created_at,
    taskIds: new Set(),
    items: [],
  };
}

function finalizeTurnGroup(group) {
  const finalOutput = [...group.items]
    .filter((item) => ["harness.output", "harness.failed", "harness.canceled"].includes(item.event_type))
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || String(b.id).localeCompare(String(a.id)))[0];
  if (finalOutput?.message) {
    group.summary = finalOutput.message;
  }
  group.phases = turnPhaseGroups(group);
  group.items.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || String(b.id).localeCompare(String(a.id)));
  return group;
}

function turnPhaseGroups(group) {
  const chronological = [...group.items].sort(
    (a, b) => String(a.created_at).localeCompare(String(b.created_at)) || String(a.id).localeCompare(String(b.id)),
  );
  const phases = [];
  let current = null;

  chronological.forEach((item) => {
    const role = phaseRole(item);
    if (role === "phase" || item.event_type === "harness.progress") {
      current = createPhaseGroup(phaseTitle(item), item);
      phases.push(current);
      current.items.push(item);
      current.updated_at = maxTimestamp(current.updated_at, item.created_at);
      return;
    }
    if (role === "final" || ["harness.output", "harness.failed", "harness.canceled"].includes(item.event_type)) {
      current = createPhaseGroup("Final answer", item);
      phases.push(current);
      current.items.push(item);
      current.updated_at = maxTimestamp(current.updated_at, item.created_at);
      return;
    }
    if (!current) {
      current = createFallbackPhaseGroup("Work", item);
      phases.push(current);
    }
    current.items.push(item);
    current.updated_at = maxTimestamp(current.updated_at, item.created_at);
  });

  phases.forEach((phase) => {
    phase.items.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || String(b.id).localeCompare(String(a.id)));
  });
  return phases.reverse();
}

function phaseRole(item) {
  return item.payload?._innie_phase?.role || "";
}

function phaseTitle(item) {
  return item.payload?._innie_phase?.title || item.message || "Progress update";
}

function createPhaseGroup(title, item) {
  return {
    id: `phase:${item.id}`,
    title,
    started_at: item.created_at,
    updated_at: item.created_at,
    items: [],
  };
}

function createFallbackPhaseGroup(title, item) {
  return createPhaseGroup(title, item);
}

function finalizeActivityGroup(group) {
  group.items.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || String(b.id).localeCompare(String(a.id)));
  return group;
}

function createActivityGroup() {
  return {
    id: "activity",
    title: "Session activity",
    summary: "Events not tied to a specific Slack reply.",
    started_at: "",
    updated_at: "",
    items: [],
  };
}

function groupSessionLogItems(items) {
  const group = createActivityGroup();
  items.forEach((item) => {
    group.items.push(item);
    if (!group.started_at || String(item.created_at).localeCompare(String(group.started_at)) < 0) {
      group.started_at = item.created_at;
    }
    group.updated_at = maxTimestamp(group.updated_at, item.created_at);
  });
  return group.items.length ? [finalizeActivityGroup(group)].reverse() : [];
}

function logSectionCard(group) {
  const count = group.items.length;
  const title = group.title;
  const expanded = isLogSectionExpanded(group.id);
  const pending = state.pendingToggleId === pendingToggleKey("log", group.id);
  const eventLabel = count === 1 ? "1 event" : `${count} events`;
  return `
    <section class="log-section ${expanded ? "open" : ""}" data-log-section="${escapeAttr(group.id)}">
      <button class="log-section-head ledger-row ${pending ? "pending" : ""}" data-log-section-toggle="${escapeAttr(group.id)}" aria-expanded="${expanded ? "true" : "false"}" aria-busy="${pending ? "true" : "false"}">
        <span class="log-section-title">${escapeHtml(title)}</span>
        <span class="log-section-summary">${escapeHtml(group.summary)}</span>
        <span class="log-section-meta">${escapeHtml(eventLabel)} · ${formatTime(group.updated_at)}</span>
        <span class="ledger-action">${pending ? spinner("Updating") : expanded ? "[CLOSE]" : "[OPEN]"}</span>
      </button>
      <div class="log-section-items ${expanded ? "" : "hidden"}">
        ${group.phases ? group.phases.map(phaseSectionCard).join("") : group.items.map(logEntryCard).join("") || `<div class="empty compact">No detailed events in this step.</div>`}
      </div>
    </section>
  `;
}

function phaseSectionCard(phase) {
  const count = phase.items.length;
  const eventLabel = count === 1 ? "1 event" : `${count} events`;
  const expanded = isPhaseSectionExpanded(phase.id);
  const pending = state.pendingToggleId === pendingToggleKey("phase", phase.id);
  return `
    <section class="phase-section ${expanded ? "open" : ""}" data-phase-section="${escapeAttr(phase.id)}">
      <button class="phase-section-head ledger-row ${pending ? "pending" : ""}" data-phase-section-toggle="${escapeAttr(phase.id)}" aria-expanded="${expanded ? "true" : "false"}" aria-busy="${pending ? "true" : "false"}">
        <span class="phase-kind">${escapeHtml(phaseKind(phase))}</span>
        <span class="phase-title">${escapeHtml(phase.title)}</span>
        <span class="phase-meta">${escapeHtml(eventLabel)} · ${formatTime(phase.updated_at)}</span>
        <span class="ledger-action">${pending ? spinner("Updating") : expanded ? "[HIDE]" : "[DETAILS]"}</span>
      </button>
      <div class="phase-section-items ${expanded ? "" : "hidden"}">
        ${phase.items.map(logEntryCard).join("")}
      </div>
    </section>
  `;
}

function logEntryCard(item) {
  const expanded = isLogEntryExpanded(item.id);
  const message = item.message || item.event_type || "Event";
  const pending = state.pendingToggleId === pendingToggleKey("entry", item.id);
  return `
    <div class="event log-entry">
      <span class="event-type">${escapeHtml(item.event_type || "event")}</span>
      <div class="log-entry-body" data-log-entry="${escapeAttr(item.id)}">
        <button class="log-entry-summary ${pending ? "pending" : ""}" data-log-entry-toggle="${escapeAttr(item.id)}" aria-expanded="${expanded ? "true" : "false"}" aria-busy="${pending ? "true" : "false"}">
          <span class="entry-text">${escapeHtml(message)}</span>
          <span class="ledger-action entry-action">${pending ? spinner("Updating") : expanded ? "[HIDE]" : "[DETAILS]"}</span>
        </button>
        <div class="log-entry-detail ${expanded ? "" : "hidden"}">
        <div class="event-message">${escapeHtml(message)}</div>
        <div class="event-head">
          <span>${escapeHtml(item.source)}</span>
          <span>${formatTime(item.created_at)}</span>
        </div>
        <pre class="expanded-payload">${escapeHtml(JSON.stringify(item.payload, null, 2))}</pre>
        </div>
      </div>
    </div>
  `;
}

function phaseKind(phase) {
  const first = phase.items[0];
  if (!first) return "Phase";
  if (first.event_type === "harness.progress") return "Progress";
  if (["harness.output", "harness.failed", "harness.canceled"].includes(first.event_type)) return "Output";
  if (first.event_type?.includes("tool")) return "Tool use";
  return "Work";
}

function isLogSectionExpanded(id) {
  return state.expandedLogSections.has(id);
}

function isPhaseSectionExpanded(id) {
  return state.expandedPhaseSections.has(id);
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

function themeToggle() {
  return `
    <button class="theme-toggle" data-toggle-theme aria-label="Switch to ${state.theme === "dark" ? "light" : "dark"} mode">
      <span class="${state.theme === "light" ? "active" : ""}">Light</span>
      <span class="${state.theme === "dark" ? "active" : ""}">Dark</span>
    </button>
  `;
}

function bind() {
  document.querySelectorAll("[data-toggle-theme]").forEach((button) => {
    button.addEventListener("click", toggleTheme);
  });
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
  document.querySelectorAll("[data-log-section-toggle]").forEach((button) => {
    button.addEventListener("click", () => runWithPendingToggle("log", button.dataset.logSectionToggle, state.expandedLogSections));
  });
  document.querySelectorAll("[data-phase-section-toggle]").forEach((button) => {
    button.addEventListener("click", () => runWithPendingToggle("phase", button.dataset.phaseSectionToggle, state.expandedPhaseSections));
  });
  document.querySelectorAll("[data-log-entry-toggle]").forEach((button) => {
    button.addEventListener("click", () => runWithPendingToggle("entry", button.dataset.logEntryToggle, state.expandedLogEntries));
  });
}

function toggleTheme() {
  state.theme = state.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = state.theme;
  window.localStorage.setItem(THEME_STORAGE_KEY, state.theme);
  render();
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
    state.expandedPhaseSections.clear();
    state.expandedLogEntries.clear();
  }
  state.selectedSessionId = sessionId;
  state.tab = "logs";
  state.sessionDetail = state.sessionDetailCache.get(sessionId) || null;
  if (updateUrl) {
    window.history.pushState({}, "", `/runs?session=${encodeURIComponent(sessionId)}`);
  }
  state.pendingSessionId = sessionId;
  render();
  await nextFrame();
  try {
    await loadSelectedSessionDetail();
  } finally {
    if (state.pendingSessionId === sessionId) state.pendingSessionId = null;
    render();
  }
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

function metric(label, value, filter, filterValue) {
  const content = `<span class="metric-label">${escapeHtml(label)}</span><span class="metric-value">${escapeHtml(value ?? 0)}</span>`;
  if (!filter || !filterValue) {
    return `<div class="metric">${content}</div>`;
  }
  const active = filterValue === state.filters[filter];
  return `
    <button
      class="metric metric-button ${active ? "active" : ""}"
      data-filter-button="${escapeAttr(filter)}"
      data-value="${escapeAttr(filterValue)}"
      aria-pressed="${filterValue === state.filters[filter] ? "true" : "false"}"
      title="Filter ${escapeAttr(label)} sessions"
    >${content}</button>
  `;
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

function toggleExpanded(set, key) {
  if (!key) return;
  setExpanded(set, key, !set.has(key));
}

async function runWithPendingToggle(kind, key, set) {
  if (!key) return;
  state.pendingToggleId = pendingToggleKey(kind, key);
  render();
  await nextFrame();
  toggleExpanded(set, key);
  state.pendingToggleId = null;
  render();
}

function pendingToggleKey(kind, key) {
  return `${kind}:${key}`;
}

function nextFrame() {
  return new Promise((resolve) => window.requestAnimationFrame(resolve));
}

function spinner(label) {
  return `<span class="spinner" aria-hidden="true"></span><span class="sr-only">${escapeHtml(label)}</span>`;
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
