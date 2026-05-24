import re
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "src" / "innie" / "dash" / "web" / "app.js"


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
