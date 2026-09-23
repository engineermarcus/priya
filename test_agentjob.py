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


if __name__ == "__main__":
    unittest.main()
