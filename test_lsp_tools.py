import asyncio
import os
import tempfile
import unittest

import live_cli
from tools.lsp import LspManager


class _FakeServer:
    def __init__(self):
        self.calls = []

    def sync(self, path):
        self.calls.append(("sync", path))
        return "file:///workspace/example.py"

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "textDocument/definition":
            return {"result": {"uri": "file:///workspace/definition.py", "range": {
                "start": {"line": 2, "character": 1}, "end": {"line": 2, "character": 5},
            }}}
        return {"result": []}


class LspToolTests(unittest.TestCase):
    def test_definition_uses_lsp_positions_and_normalizes_locations(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "example.py")
            with open(source, "w", encoding="utf-8") as output:
                output.write("answer = 42\n")
            manager = LspManager(root)
            fake = _FakeServer()
            manager._server_for = lambda _path: fake
            result = manager.query(
                {"action": "definition", "path": source, "line": 4, "character": 7},
                lambda path: os.path.realpath(path),
            )
            self.assertEqual(result["results"][0]["path"], "/workspace/definition.py")
            self.assertEqual(result["results"][0]["range"]["start"], {"line": 3, "character": 1})
            self.assertEqual(fake.calls[1][1]["position"], {"line": 3, "character": 7})

    def test_lsp_is_available_for_read_only_plan_mode(self):
        async def verify():
            loop = live_cli.TextLoop()
            await loop.enter_plan_mode({})
            self.assertIsNone(loop._tool_blocked_by_plan_mode("LSP"))
            status = await loop.lsp_query({"action": "status"})
            self.assertEqual(status["root"], loop._current_workdir)
            loop._lsp.reset()

        asyncio.run(verify())

    def test_unsupported_file_reports_a_semantic_server_error(self):
        async def verify(path):
            loop = live_cli.TextLoop()
            result = await loop.lsp_query({"action": "definition", "path": path, "line": 1})
            self.assertIn("no LSP language server is configured", result["error"])
            loop._lsp.reset()

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as source:
            source.write("text\n")
            path = source.name
        try:
            asyncio.run(verify(path))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
