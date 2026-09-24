import asyncio
import unittest

import live_cli
from tools.mcp import McpManager


class _FakeMcpServer:
    def __init__(self):
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "resources/list" and not params:
            return {"result": {"resources": [{"uri": "db://orders", "name": "Orders", "mimeType": "application/json"}], "nextCursor": "second"}}
        if method == "resources/list":
            return {"result": {"resources": [{"uri": "db://users", "name": "Users"}]}}
        return {"result": {"resourceTemplates": [{"uriTemplate": "db://orders/{id}", "name": "Order"}]}}


class McpResourceToolTests(unittest.TestCase):
    def test_catalog_paginates_and_labels_resources_with_server(self):
        manager = McpManager("/workspace", config={"database": {"command": "example-mcp"}})
        server = _FakeMcpServer()
        manager._server = lambda _name, _config: server
        result = manager.list_resources()
        self.assertEqual(result["count"], 2)
        self.assertEqual([item["server"] for item in result["resources"]], ["database", "database"])
        self.assertEqual(result["resources"][1]["uri"], "db://users")
        self.assertEqual(result["resource_templates"][0]["server"], "database")
        self.assertEqual(server.calls[1], ("resources/list", {"cursor": "second"}))

    def test_no_configured_servers_returns_empty_catalog_and_plan_mode_allows_it(self):
        async def verify():
            loop = live_cli.TextLoop()
            await loop.enter_plan_mode({})
            self.assertIsNone(loop._tool_blocked_by_plan_mode("ListMcpResourcesTool"))
            result = await loop.list_mcp_resources({})
            self.assertEqual(result["servers"], [])
            self.assertEqual(result["resources"], [])
            loop._mcp.reset()

        asyncio.run(verify())


if __name__ == "__main__":
    unittest.main()
