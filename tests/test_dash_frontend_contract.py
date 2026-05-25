import re
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "src" / "innie" / "dash" / "web" / "app.js"
APP_CSS = Path(__file__).resolve().parents[1] / "src" / "innie" / "dash" / "web" / "styles.css"
APP_HTML = Path(__file__).resolve().parents[1] / "src" / "innie" / "dash" / "web" / "index.html"


class FrontendContractTest(unittest.TestCase):
    def test_runs_shell_lazy_loads_session_detail(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("sessionDetailCache: new Map()", source)
        self.assertIn("async function loadSelectedSessionDetail", source)
        self.assertIn("detail-placeholder", source)

        refresh_body = re.search(r"async function refresh\(\) \{(?P<body>.*?)\n\}", source, re.S)
        self.assertIsNotNone(refresh_body)
        self.assertNotIn("/api/sessions/${", refresh_body.group("body"))

    def test_session_logs_are_newest_first_without_explanatory_panels(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("String(b.created_at).localeCompare(String(a.created_at))", source)
        self.assertNotIn("Chronological session log", source)
        self.assertNotIn("Innie logging advice", source)
        self.assertNotIn("log-explainer", source)
        self.assertNotIn("log-advice", source)

    def test_session_logs_group_turns_into_folded_sections(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function sessionTurnGroups(detail)", source)
        self.assertIn("function createTurnGroup(row, turnNumber)", source)
        self.assertIn(".reverse();", source)
        self.assertIn("isLogSectionExpanded(group.id)", source)
        self.assertIn("expandedLogSections: new Set()", source)
        self.assertIn("data-log-section", source)

    def test_session_logs_group_replies_by_turn(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function sessionTurnGroups(detail)", source)
        self.assertIn("buildTurnIndex(detail)", source)
        self.assertIn("item.payload.inbox_id", source)
        self.assertIn("item.task_id", source)
        self.assertIn('title: `Reply ${turnNumber}`', source)
        self.assertIn("finalOutput", source)

    def test_session_logs_group_turn_events_into_phase_sections(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function turnPhaseGroups(group)", source)
        self.assertIn("function phaseRole(item)", source)
        self.assertIn('_innie_phase', source)
        self.assertIn('item.event_type === "harness.progress"', source)
        self.assertIn('createFallbackPhaseGroup("Work", item)', source)
        self.assertIn("phase-section", source)
        self.assertIn("phase.items.map(logEntryCard)", source)
        self.assertIn("phases.reverse()", source)

    def test_phase_sections_preserve_expansion_across_refreshes(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("expandedPhaseSections: new Set()", source)
        self.assertIn("isPhaseSectionExpanded(phase.id)", source)
        self.assertIn("data-phase-section", source)
        self.assertIn("state.expandedPhaseSections", source)

    def test_session_log_entries_truncate_and_expand_independently(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function logEntryCard(item)", source)
        self.assertIn("isLogEntryExpanded(item.id)", source)
        self.assertIn("expandedLogEntries: new Set()", source)
        self.assertIn("data-log-entry", source)
        self.assertIn("entry-text", source)
        self.assertIn("expanded-payload", source)

    def test_session_log_entry_layout_prevents_text_overlap(self) -> None:
        source = APP_CSS.read_text(encoding="utf-8")

        self.assertRegex(source, r"\.event\s*\{[^}]*grid-template-columns:\s*minmax\(120px,\s*128px\)\s+minmax\(0,\s*1fr\)")
        self.assertRegex(source, r"\.log-entry-summary\s*\{[^}]*min-width:\s*0")
        self.assertRegex(source, r"\.event-type\s*\{[^}]*overflow:\s*hidden")
        self.assertRegex(source, r"\.event-message\s*\{[^}]*overflow-wrap:\s*anywhere")

    def test_selected_session_refreshes_every_second_without_live_toggle(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertNotIn("sessionLive", source)
        self.assertNotIn("data-toggle-live", source)
        self.assertNotIn("Live on", source)
        self.assertNotIn("Live off", source)
        self.assertIn("shellTimer: null", source)
        self.assertIn("sessionTimer: null", source)
        self.assertIn("window.setInterval(refreshVisibleRoute, 3000)", source)
        self.assertIn("window.setInterval(refreshLiveSession, 1000)", source)
        self.assertIn("async function refreshLiveSession()", source)
        self.assertIn("loadSelectedSessionDetail({ force: true, renderAfter: false })", source)

    def test_run_status_metrics_apply_matching_status_filter(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        styles = APP_CSS.read_text(encoding="utf-8")

        self.assertIn('metric("Sessions", counts.sessions, "status", "all")', source)
        self.assertIn('metric("Active", counts.running_sessions, "status", "running")', source)
        self.assertIn('metric("Queued", counts.queued_sessions, "status", "queued")', source)
        self.assertIn('metric("Failed", counts.failed_tasks, "status", "failed")', source)
        self.assertNotIn('metric("Workers"', source)
        self.assertNotIn('metric("Stale"', source)
        self.assertIn('segment("status", "queued", "Queued", state.filters.status)', source)
        self.assertRegex(source, r"function metric\(label, value, filter, filterValue\)")
        self.assertIn('data-filter-button="${escapeAttr(filter)}"', source)
        self.assertIn('data-value="${escapeAttr(filterValue)}"', source)
        self.assertIn('aria-pressed="${filterValue === state.filters[filter] ? "true" : "false"}"', source)
        self.assertIn(".metric-button", styles)
        self.assertIn(".metric-button.active", styles)

    def test_session_switcher_badge_shows_failed_task_status(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn('pending ? spinner("Loading session") : chip(sessionListStatus(row))', source)
        self.assertIn("function sessionListStatus(row)", source)
        self.assertIn('if (row.latest_task_status === "failed") return "failed";', source)
        self.assertIn("return row.status;", source)

    def test_open_slack_uses_workspace_neutral_redirect(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("https://slack.com/app_redirect?", source)
        self.assertIn('params.set("channel", channel)', source)
        self.assertIn('params.set("message_ts", rootTs)', source)
        self.assertNotIn("/archives/", source)

    def test_slow_session_actions_show_pending_indicators(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        styles = APP_CSS.read_text(encoding="utf-8")

        self.assertIn("pendingSessionId: null", source)
        self.assertIn("pendingToggleId: null", source)
        self.assertIn("function spinner(label)", source)
        self.assertIn("async function runWithPendingToggle", source)
        self.assertIn("await nextFrame()", source)
        self.assertIn("aria-busy", source)
        self.assertIn(".spinner", styles)
        self.assertIn("@keyframes spin", styles)

    def test_left_rail_only_exposes_runs_and_system_with_innie_icon(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        routes = re.search(r"const routes = \[(?P<body>.*?)\];", source, re.S)
        self.assertIsNotNone(routes)
        route_body = routes.group("body")
        self.assertIn('id: "runs"', route_body)
        self.assertIn('id: "system"', route_body)
        self.assertNotIn('id: "events"', route_body)
        self.assertNotIn('id: "health"', route_body)
        self.assertNotIn('id: "settings"', route_body)
        self.assertIn('path: "/system"', route_body)

        self.assertIn('src="/assets/innie-app-icon.svg"', source)
        self.assertIn('class="mark-icon"', source)
        self.assertIn('if (path === "/system") return { name: "system" };', source)
        self.assertIn("function systemPage()", source)
        self.assertNotIn("function eventsPage()", source)
        self.assertNotIn("function healthPage()", source)
        self.assertNotIn("function settingsPage()", source)

    def test_dash_loads_nothing_fonts(self) -> None:
        source = APP_HTML.read_text(encoding="utf-8")

        self.assertIn("fonts.googleapis.com", source)
        self.assertIn("fonts.gstatic.com", source)
        self.assertIn("family=Doto", source)
        self.assertIn("family=Space+Grotesk", source)
        self.assertIn("family=Space+Mono", source)

    def test_dash_has_persistent_light_dark_theme_toggle(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn('theme: "light"', source)
        self.assertIn('const THEME_STORAGE_KEY = "innie.dash.theme"', source)
        self.assertIn("function initializeTheme()", source)
        self.assertIn("window.localStorage.getItem(THEME_STORAGE_KEY)", source)
        self.assertIn('storedTheme === "light" || storedTheme === "dark" ? storedTheme : "light"', source)
        self.assertIn('document.documentElement.dataset.theme = state.theme', source)
        self.assertIn("function themeToggle()", source)
        self.assertIn("data-toggle-theme", source)
        self.assertIn("function toggleTheme()", source)
        self.assertIn("window.localStorage.setItem(THEME_STORAGE_KEY, state.theme)", source)

    def test_dash_uses_nothing_design_tokens(self) -> None:
        source = APP_CSS.read_text(encoding="utf-8")

        self.assertIn("--black: #000000", source)
        self.assertIn("--paper: #f5f5f5", source)
        self.assertIn("--accent: #d71921", source)
        self.assertIn("--font-display: Doto", source)
        self.assertIn("--font-ui: \"Space Grotesk\"", source)
        self.assertIn("--font-mono: \"Space Mono\"", source)
        self.assertIn("[data-theme=\"light\"]", source)
        self.assertIn("[data-theme=\"dark\"]", source)
        self.assertNotIn("#2563eb", source)

    def test_timeline_uses_ledger_buttons_not_native_disclosure_arrows(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        styles = APP_CSS.read_text(encoding="utf-8")

        self.assertNotIn("<details", source)
        self.assertNotIn("<summary", source)
        self.assertNotIn("section-chevron", source)
        self.assertIn("data-log-section-toggle", source)
        self.assertIn("data-phase-section-toggle", source)
        self.assertIn("data-log-entry-toggle", source)
        self.assertIn("ledger-action", source)
        self.assertIn("[OPEN]", source)
        self.assertIn("[CLOSE]", source)
        self.assertIn("[DETAILS]", source)
        self.assertIn("[HIDE]", source)
        self.assertIn(".timeline-ledger", styles)
        self.assertIn(".ledger-action", styles)
        self.assertNotIn("border-radius: var(--radius)", styles)


if __name__ == "__main__":
    unittest.main()
