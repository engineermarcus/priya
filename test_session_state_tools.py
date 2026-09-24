import asyncio
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import live_cli


class SessionStateToolTests(unittest.TestCase):
    def test_session_starts_in_the_callers_directory(self):
        with tempfile.TemporaryDirectory() as called_from:
            with mock.patch.object(live_cli, "SESSION_DIR", os.path.realpath(called_from)):
                loop = live_cli.TextLoop()
            self.assertEqual(loop._base_workdir, os.path.realpath(called_from))
            self.assertEqual(loop._current_workdir, os.path.realpath(called_from))

    def test_plan_mode_allows_read_but_blocks_writes_and_commands(self):
        async def verify(path):
            loop = live_cli.TextLoop()
            entered = await loop.enter_plan_mode({})
            self.assertTrue(entered["plan_mode"])

            read = await loop.read_file({"path": path})
            self.assertEqual(read["content"], "before\n")

            edit = await loop.edit_file({
                "path": path,
                "old_string": "before",
                "new_string": "after",
            })
            self.assertIn("blocked in Plan Mode", edit["error"])

            bash = await loop.run_active_bash({"command": "echo should-not-run"}, None)
            self.assertIn("blocked in Plan Mode", bash["error"])

            exited = await loop.exit_plan_mode({})
            self.assertFalse(exited["plan_mode"])

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as source:
            source.write("before\n")
            path = source.name
        try:
            asyncio.run(verify(path))
        finally:
            os.unlink(path)

    def test_worktree_scopes_relative_paths_and_clears_read_approvals(self):
        async def verify(repo):
            loop = live_cli.TextLoop()
            entered = await loop.enter_worktree({"path": repo})
            self.assertEqual(entered["workdir"], os.path.realpath(repo))

            read = await loop.read_file({"path": "sample.txt"})
            self.assertEqual(read["path"], os.path.realpath(os.path.join(repo, "sample.txt")))
            self.assertEqual(read["content"], "tree\n")

            exited = await loop.exit_worktree({})
            self.assertEqual(exited["workdir"], loop._base_workdir)

            edit = await loop.edit_file({
                "path": os.path.join(repo, "sample.txt"),
                "old_string": "tree",
                "new_string": "changed",
            })
            self.assertIn("requires Read", edit["error"])

        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with open(os.path.join(repo, "sample.txt"), "w", encoding="utf-8") as source:
                source.write("tree\n")
            asyncio.run(verify(repo))

    def test_glob_matches_live_scoped_files_and_supports_exclusions(self):
        async def verify(repo):
            loop = live_cli.TextLoop()
            await loop.enter_worktree({"path": repo})

            result = await loop.glob_files({
                "patterns": ["src/**/*.{js,ts}", "!src/**/skip.js"],
            })
            self.assertEqual(result["files"], [
                os.path.realpath(os.path.join(repo, "src", "nested", "child.ts")),
                os.path.realpath(os.path.join(repo, "src", "root.js")),
            ])
            self.assertEqual(result["count"], 2)
            self.assertFalse(result["truncated"])

            direct = await loop.glob_files({"patterns": ["src/*.js"]})
            self.assertEqual(direct["files"], [
                os.path.realpath(os.path.join(repo, "src", "root.js")),
            ])

            await loop.enter_plan_mode({})
            limited = await loop.glob_files({"patterns": ["src/**/*"], "max_results": 1})
            self.assertEqual(limited["count"], 4)
            self.assertTrue(limited["truncated"])

        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            paths = [
                "src/root.js",
                "src/nested/child.ts",
                "src/nested/skip.js",
                "src/nested/notes.txt",
            ]
            for relative_path in paths:
                absolute_path = os.path.join(repo, relative_path)
                os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
                with open(absolute_path, "w", encoding="utf-8") as source:
                    source.write(relative_path + "\n")
            asyncio.run(verify(repo))

    def test_grep_returns_structured_scoped_matches_in_plan_mode(self):
        async def verify(repo):
            loop = live_cli.TextLoop()
            await loop.enter_worktree({"path": repo})

            result = await loop.grep_files({"pattern": "needle", "path": "src"})
            self.assertEqual(result["matches"], [{
                "path": os.path.realpath(os.path.join(repo, "src", "sample.py")),
                "line_number": 1,
                "line": "needle = 1",
            }])
            self.assertFalse(result["truncated"])

            await loop.enter_plan_mode({})
            insensitive = await loop.grep_files({
                "pattern": "needle", "path": "src", "case_sensitive": False, "max_results": 1,
            })
            self.assertEqual(insensitive["count"], 1)
            self.assertTrue(insensitive["truncated"])
            self.assertEqual(insensitive["matches"][0]["line_number"], 1)

        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            source_dir = os.path.join(repo, "src")
            os.mkdir(source_dir)
            with open(os.path.join(source_dir, "sample.py"), "w", encoding="utf-8") as source:
                source.write("needle = 1\nNEEDLE = 2\n")
            with open(os.path.join(repo, "outside.py"), "w", encoding="utf-8") as source:
                source.write("needle = 3\n")
            asyncio.run(verify(repo))


if __name__ == "__main__":
    unittest.main()
