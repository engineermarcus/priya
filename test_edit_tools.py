import asyncio
import contextlib
import io
import os
import tempfile
import unittest

import live_cli


class EditToolTests(unittest.TestCase):
    def test_requires_read_and_rejects_ambiguous_match(self):
        async def verify(path):
            loop = live_cli.TextLoop()
            edit = {"path": path, "old_string": "value", "new_string": "changed"}
            self.assertIn("requires Read", (await loop.edit_file(edit))["error"])
            await loop.read_file({"path": path})
            result = await loop.edit_file(edit)
            self.assertIn("occurs 2 times", result["error"])

            async def approve(_path, _diff):
                return {"approved": True}

            loop.request_edit_approval = approve
            result = await loop.edit_file({**edit, "replace_all": True})
            self.assertEqual(result["replacements"], 2)
            with open(path, encoding="utf-8", newline="") as source:
                self.assertEqual(source.read(), "changed\nchanged\n")

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as source:
            source.write("value\nvalue\n")
            path = source.name
        try:
            asyncio.run(verify(path))
        finally:
            os.unlink(path)

    def test_approved_edit_is_precise_and_rejected_edit_is_unchanged(self):
        async def verify(path):
            loop = live_cli.TextLoop()
            await loop.read_file({"path": path})

            with contextlib.redirect_stdout(io.StringIO()):
                pending_edit = asyncio.create_task(loop.edit_file({
                    "path": path, "old_string": "enabled = false", "new_string": "enabled = true",
                }))
                await asyncio.sleep(0)
                self.assertIsNotNone(loop._pending_edit)
                loop._resolve_pending_edit({"id": loop._pending_edit["id"], "approved": True})
                result = await pending_edit
            self.assertTrue(result["approved"])
            self.assertEqual(result["replacements"], 1)
            self.assertIn("-enabled = false", result["diff"])
            self.assertIn("+enabled = true", result["diff"])
            with open(path, encoding="utf-8", newline="") as source:
                self.assertEqual(source.read(), "header\nenabled = true\nfooter\n")

            async def reject(_path, _diff):
                return {"approved": False}

            loop.request_edit_approval = reject
            result = await loop.edit_file({
                "path": path, "old_string": "footer", "new_string": "changed",
            })
            self.assertFalse(result["approved"])
            with open(path, encoding="utf-8", newline="") as source:
                self.assertEqual(source.read(), "header\nenabled = true\nfooter\n")

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, newline="") as source:
            source.write("header\nenabled = false\nfooter\n")
            path = source.name
        try:
            asyncio.run(verify(path))
        finally:
            os.unlink(path)

    def test_file_changed_while_waiting_is_not_overwritten(self):
        async def verify(path):
            loop = live_cli.TextLoop()
            await loop.read_file({"path": path})

            async def approve_after_external_change(_path, _diff):
                with open(path, "w", encoding="utf-8", newline="") as destination:
                    destination.write("external change\n")
                return {"approved": True}

            loop.request_edit_approval = approve_after_external_change
            result = await loop.edit_file({
                "path": path, "old_string": "before", "new_string": "after",
            })
            self.assertIn("changed while awaiting approval", result["error"])
            with open(path, encoding="utf-8", newline="") as source:
                self.assertEqual(source.read(), "external change\n")

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, newline="") as source:
            source.write("before\n")
            path = source.name
        try:
            asyncio.run(verify(path))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
