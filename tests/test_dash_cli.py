from __future__ import annotations

import unittest
from pathlib import Path

from innie.cli import build_parser


class DashCliTest(unittest.TestCase):
    def test_dash_command_starts_server_with_defaults(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["dash"])

        self.assertEqual(args.command, "dash")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)
        self.assertIsNone(args.web_dir)

    def test_dash_accepts_workspace_host_port_and_web_dir(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "--workspace",
                "/tmp/work",
                "dash",
                "--host",
                "0.0.0.0",
                "--port",
                "9999",
                "--web-dir",
                "/tmp/web",
            ]
        )

        self.assertEqual(args.workspace, Path("/tmp/work"))
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9999)
        self.assertEqual(args.web_dir, Path("/tmp/web"))

    def test_dash_server_remains_supported_as_alias(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["dash", "server", "--port", "9999"])

        self.assertEqual(args.command, "dash")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 9999)


if __name__ == "__main__":
    unittest.main()
