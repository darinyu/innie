import tempfile
import unittest
from pathlib import Path

from innie.dash.logs import LocalFileLogSource


class LocalFileLogSourceTest(unittest.TestCase):
    def test_missing_log_returns_empty_page(self) -> None:
        source = LocalFileLogSource(Path("/tmp/innie-dash-missing-log-file.log"))

        page = source.tail(after_offset=0)

        self.assertEqual(page["lines"], [])
        self.assertEqual(page["next"]["logOffset"], 0)
        self.assertFalse(page["exists"])

    def test_tail_reads_lines_and_returns_next_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.log"
            path.write_text("one\ntwo\n", encoding="utf-8")
            source = LocalFileLogSource(path)

            first = source.tail(after_offset=0)
            path.write_text("one\ntwo\nthree\n", encoding="utf-8")
            second = source.tail(after_offset=first["next"]["logOffset"])

        self.assertEqual(first["lines"], ["one", "two"])
        self.assertEqual(second["lines"], ["three"])
        self.assertGreater(second["next"]["logOffset"], first["next"]["logOffset"])


if __name__ == "__main__":
    unittest.main()
