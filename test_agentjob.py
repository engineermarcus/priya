import contextlib
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import job_runner
import live_cli


class AgentJobJournalTests(unittest.TestCase):
    def test_log_returns_only_events_after_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            original_jobs_dir = job_runner.JOBS_DIR
            job_runner.JOBS_DIR = Path(directory)
            try:
                job_id = "journal-test"
                paths = job_runner.paths(job_id)
                paths["base"].mkdir()
                paths["status"].write_text(json.dumps({"job_id": job_id, "status": "running", "event_cursor": 0}))
                paths["events"].write_text("")
                with contextlib.redirect_stdout(io.StringIO()):
                    first = job_runner.record_event(job_id, "narration", "Inspecting the project")
                    second = job_runner.record_event(job_id, "command", "$ pytest")

                    first_log = io.StringIO()
                    with contextlib.redirect_stdout(first_log):
                        job_runner.log(job_id)
                    next_log = io.StringIO()
                    with contextlib.redirect_stdout(next_log):
                        job_runner.log(job_id, first["cursor"])

                self.assertEqual([event["message"] for event in json.loads(first_log.getvalue())["events"]],
                                 ["Inspecting the project", "$ pytest"])
                incremental = json.loads(next_log.getvalue())
                self.assertEqual(incremental["events"], [second])
                self.assertEqual(incremental["next_cursor"], second["cursor"])
            finally:
                job_runner.JOBS_DIR = original_jobs_dir

    def test_live_session_sends_are_serialized(self):
        class RecordingSession:
            def __init__(self):
                self.active = 0
                self.maximum_active = 0

            async def _record(self):
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                await asyncio.sleep(0)
                self.active -= 1

            async def send_client_content(self, **_kwargs):
                await self._record()

            async def send_tool_response(self, **_kwargs):
                await self._record()

        async def verify():
            loop = live_cli.TextLoop()
            loop.session = RecordingSession()
            await asyncio.gather(
                loop._send_client_content(turns={}, turn_complete=True),
                loop._send_tool_response(function_responses=[]),
            )
            self.assertEqual(loop.session.maximum_active, 1)

        asyncio.run(verify())

    def test_agentjob_progress_hides_internal_journal_events(self):
        self.assertEqual(
            live_cli.agentjob_progress_message({"kind": "narration", "message": "Checking the layout"}),
            "Checking the layout",
        )
        self.assertIsNone(live_cli.agentjob_progress_message({"kind": "command", "message": "$ npm test"}))
        self.assertIsNone(live_cli.agentjob_progress_message({"kind": "read", "message": "Read App.jsx"}))
        self.assertIsNone(live_cli.agentjob_progress_message({"kind": "started", "message": "Coding job started"}))
        self.assertIsNone(live_cli.agentjob_progress_message({"kind": "done", "message": "Agent reported completion"}))

    def test_plain_cat_is_bounded_to_a_small_preview(self):
        self.assertEqual(live_cli.bound_plain_cat("cat src/App.jsx"), "head -n 16 -- src/App.jsx")
        self.assertEqual(job_runner.bound_plain_cat("cat 'a file.txt'"), "head -n 16 -- 'a file.txt'")
        self.assertEqual(live_cli.bound_plain_cat("cat source.txt | wc -l"), "cat source.txt | wc -l")

    def test_done_requires_a_standalone_line(self):
        self.assertTrue(job_runner.has_done_signal("Tests passed.\n\nDONE\n"))
        self.assertFalse(job_runner.has_done_signal("I am not DONE until tests pass."))
        self.assertFalse(job_runner.has_done_signal("DONE means I finished the test."))

    def test_changed_job_cannot_finish_before_review_and_verification(self):
        evidence = job_runner.CompletionEvidence()
        evidence.record_change({"source.py"})
        self.assertEqual(
            evidence.missing(),
            [
                "review every changed file with Read or inspect the Git diff/status",
                "run a relevant test, build, lint, type check, or smoke check",
            ],
        )

        evidence.record_command("git diff --check", 0)
        self.assertEqual(
            evidence.missing(),
            ["run a relevant test, build, lint, type check, or smoke check"],
        )
        evidence.record_command("git diff --check", 1)
        evidence.record_command("python -m pytest -q", 1)
        self.assertEqual(len(evidence.missing()), 1)
        evidence.record_command("python -m pytest -q", 0)
        self.assertEqual(evidence.missing(), [])

    def test_later_edit_requires_fresh_review_and_verification(self):
        evidence = job_runner.CompletionEvidence()
        evidence.record_change({"source.py"})
        evidence.record_read("source.py")
        evidence.record_command("npm run test", 0)
        self.assertEqual(evidence.missing(), [])

        evidence.record_change({"test_source.py"})
        self.assertEqual(len(evidence.missing()), 2)

    def test_review_requires_all_changed_files_to_be_read(self):
        evidence = job_runner.CompletionEvidence()
        evidence.record_change({"source.py", "test_source.py"})
        evidence.record_read("source.py")
        evidence.record_command("python -m pytest -q", 0)
        self.assertEqual(
            evidence.missing(),
            ["review every changed file with Read or inspect the Git diff/status"],
        )

    def test_project_snapshot_detects_added_and_changed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = job_runner.project_snapshot(root)
            (root / "source.py").write_text("value = 1\n")
            added = job_runner.project_snapshot(root)
            (root / "source.py").write_text("value = 2\n")
            changed = job_runner.project_snapshot(root)
            self.assertNotEqual(before, added)
            self.assertNotEqual(added, changed)


if __name__ == "__main__":
    unittest.main()
