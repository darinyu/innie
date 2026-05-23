from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.run_logging import RunLogger


class RunLoggerTest(unittest.TestCase):
    def test_emit_writes_to_stdout_callback_and_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            logger = RunLogger(Path(tmp), output=printed.append)

            logger.emit("hello log")

            self.assertEqual(["hello log"], printed)
            self.assertIn("hello log", logger.path.read_text(encoding="utf-8"))

    def test_emit_rotates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp), output=lambda _line: None, max_bytes=20, backup_count=2)
            logger.path.parent.mkdir(parents=True)
            logger.path.write_text("old log line\n", encoding="utf-8")

            logger.emit("new log line")

            self.assertIn("new log line", logger.path.read_text(encoding="utf-8"))
            self.assertIn("old log line", logger.path.with_name("innie.log.1").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
