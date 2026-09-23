import asyncio
import unittest
from datetime import datetime, timedelta

import live_cli


class _RecordingSession:
    def __init__(self):
        self.calls = []

    async def send_client_content(self, **kwargs):
        self.calls.append(kwargs)


class CronQueueSafetyTests(unittest.TestCase):
    def test_delete_cancels_a_due_queued_job(self):
        async def verify():
            loop = live_cli.TextLoop()
            created = await loop.cron_create({
                "cron": "* * * * *", "prompt": "do not run", "recurring": False,
            })
            job_id = created["job_id"]
            await loop._queue_cron_job(loop._cron_jobs[job_id])

            self.assertEqual(
                await loop.cron_delete({"job_id": job_id}),
                {"deleted": True, "job_id": job_id},
            )
            loop.session = _RecordingSession()
            scheduler = asyncio.create_task(loop.scheduler_loop())
            await asyncio.sleep(0.05)
            scheduler.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await scheduler

            self.assertEqual(loop.session.calls, [])
            self.assertTrue(loop._scheduled_prompts.empty())
            self.assertNotIn(job_id, loop._cron_jobs)
            self.assertNotIn(job_id, loop._cancelled_queued_cron_jobs)

        asyncio.run(verify())

    def test_scheduler_drops_an_expired_queued_job(self):
        async def verify():
            loop = live_cli.TextLoop()
            created = await loop.cron_create({
                "cron": "* * * * *", "prompt": "expired", "recurring": False,
            })
            job = loop._cron_jobs[created["job_id"]]
            job["expires_at"] = datetime.now().astimezone() - timedelta(seconds=1)
            await loop._queue_cron_job(job)
            loop.session = _RecordingSession()

            scheduler = asyncio.create_task(loop.scheduler_loop())
            await asyncio.sleep(0.05)
            scheduler.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await scheduler

            self.assertEqual(loop.session.calls, [])
            self.assertTrue(loop._scheduled_prompts.empty())

        asyncio.run(verify())


if __name__ == "__main__":
    unittest.main()
