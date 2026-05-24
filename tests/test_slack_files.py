from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.slack_events import SlackTrigger
from innie.slack_files import (
    SlackFileDownloadResult,
    format_file_prompt_sections,
    list_files_for_inbox,
    stage_slack_files_for_trigger,
)


class FakeFileClient:
    def __init__(self, *, failures: dict[str, Exception] | None = None) -> None:
        self.failures = failures or {}
        self.downloads: list[tuple[str, Path]] = []

    def download_file(self, url: str, destination: Path) -> SlackFileDownloadResult:
        self.downloads.append((url, destination))
        if url in self.failures:
            exc = self.failures[url]
            return SlackFileDownloadResult(error=str(exc) or exc.__class__.__name__)
        destination.write_bytes(f"contents for {url}".encode("utf-8"))
        return SlackFileDownloadResult(byte_count=destination.stat().st_size)


def trigger_with_files(event_id: str = "EvFile") -> SlackTrigger:
    return SlackTrigger(
        event_id=event_id,
        trigger_type="dm",
        channel_id="D1",
        message_ts="100.1",
        thread_ts=None,
        sender_user_id="U1",
        text="inspect these",
        payload={
            "event_id": event_id,
            "event": {
                "files": [
                    {
                        "id": "F1",
                        "name": "../report final.pdf",
                        "mimetype": "application/pdf",
                        "filetype": "pdf",
                        "url_private_download": "https://files.example/F1",
                    },
                    {
                        "id": "F2",
                        "name": "report final.pdf",
                        "mimetype": "application/pdf",
                        "filetype": "pdf",
                        "url_private_download": "https://files.example/F2",
                    },
                    {
                        "id": "F3",
                        "name": "broken.txt",
                        "mimetype": "text/plain",
                        "filetype": "text",
                        "url_private_download": "https://files.example/F3",
                    },
                ]
            },
        },
    )


def seed_session(db) -> None:
    db.execute(
        """
        INSERT INTO sessions(id, slack_channel_id, slack_root_ts, trigger_type, output_target, status, harness_id)
        VALUES('sess_1', 'D1', '100.1', 'dm', 'slack:D1:100.1', 'new', 'codex')
        """
    )


class SlackFilesTest(unittest.TestCase):
    def test_stage_slack_files_persists_successes_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            seed_session(db)
            trigger = trigger_with_files()
            client = FakeFileClient(failures={"https://files.example/F3": RuntimeError("not_allowed")})

            records = stage_slack_files_for_trigger(
                db,
                workspace=workspace,
                session_id="sess_1",
                trigger=trigger,
                file_client=client,
            )

            self.assertEqual(["F1", "F2", "F3"], [record.slack_file_id for record in records])
            self.assertEqual(["staged", "staged", "failed"], [record.status for record in records])
            self.assertEqual(
                [
                    workspace / ".innie" / "files" / "sess_1" / "EvFile" / "report_final.pdf",
                    workspace / ".innie" / "files" / "sess_1" / "EvFile" / "report_final-2.pdf",
                ],
                [Path(record.local_path) for record in records if record.status == "staged"],
            )
            self.assertEqual("not_allowed", records[2].error)
            self.assertTrue((workspace / ".innie" / "files" / "sess_1" / "EvFile" / "report_final.pdf").is_file())
            self.assertEqual(3, db.execute("SELECT COUNT(*) AS count FROM slack_files").fetchone()["count"])

    def test_format_file_prompt_sections_lists_paths_and_warnings_for_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            seed_session(db)
            trigger = trigger_with_files()
            stage_slack_files_for_trigger(
                db,
                workspace=workspace,
                session_id="sess_1",
                trigger=trigger,
                file_client=FakeFileClient(failures={"https://files.example/F3": RuntimeError("not_allowed")}),
            )

            records = list_files_for_inbox(db, session_id="sess_1", slack_event_id="EvFile")
            sections = format_file_prompt_sections(records)

            self.assertIn("Attached files:", sections)
            self.assertIn(str(workspace / ".innie" / "files" / "sess_1" / "EvFile" / "report_final.pdf"), sections)
            self.assertIn(str(workspace / ".innie" / "files" / "sess_1" / "EvFile" / "report_final-2.pdf"), sections)
            self.assertIn("Attachment warnings:", sections)
            self.assertIn("- broken.txt: download failed: not_allowed", sections)

    def test_stage_slack_files_is_idempotent_for_same_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            seed_session(db)
            trigger = trigger_with_files()
            client = FakeFileClient()

            stage_slack_files_for_trigger(db, workspace=workspace, session_id="sess_1", trigger=trigger, file_client=client)
            stage_slack_files_for_trigger(db, workspace=workspace, session_id="sess_1", trigger=trigger, file_client=client)

            self.assertEqual(3, db.execute("SELECT COUNT(*) AS count FROM slack_files").fetchone()["count"])
            self.assertEqual(3, len(client.downloads))

    def test_stage_slack_files_stages_untruncated_text_preview_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            seed_session(db)
            trigger = SlackTrigger(
                event_id="EvSnippet",
                trigger_type="dm",
                channel_id="D1",
                message_ts="100.1",
                thread_ts=None,
                sender_user_id="U1",
                text="read this",
                payload={
                    "event_id": "EvSnippet",
                    "event": {
                        "files": [
                            {
                                "id": "F1",
                                "name": "test_codex.txt",
                                "mimetype": "text/plain",
                                "filetype": "text",
                                "mode": "snippet",
                                "preview": "helloworld\n",
                                "preview_is_truncated": False,
                                "url_private_download": "https://files.example/F1",
                            },
                        ]
                    },
                },
            )

            client = FakeFileClient()

            records = stage_slack_files_for_trigger(
                db,
                workspace=workspace,
                session_id="sess_1",
                trigger=trigger,
                file_client=client,
            )

            self.assertEqual(["staged"], [record.status for record in records])
            self.assertEqual("helloworld\n", Path(records[0].local_path).read_text())
            self.assertEqual(len("helloworld\n".encode("utf-8")), records[0].byte_count)
            self.assertEqual([], client.downloads)


if __name__ == "__main__":
    unittest.main()
