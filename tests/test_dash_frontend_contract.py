import re
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "src" / "innie" / "dash" / "web" / "app.js"
APP_CSS = Path(__file__).resolve().parents[1] / "src" / "innie" / "dash" / "web" / "styles.css"


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

    def test_session_logs_group_progress_into_folded_sections(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function groupSessionLogItems(items)", source)
        self.assertIn('item.event_type === "harness.progress"', source)
        self.assertIn("groups.reverse()", source)
        self.assertIn('class="phase-card', source)
        self.assertIn("isLogSectionExpanded(group.id)", source)
        self.assertIn("expandedLogSections: new Set()", source)
        self.assertIn("data-log-section", source)

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
        self.assertRegex(source, r"\.log-entry\s+details\s*\{[^}]*overflow:\s*hidden")
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


if __name__ == "__main__":
    unittest.main()
