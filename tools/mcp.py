"""Persistent stdio MCP connections and live resource discovery for Priya."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


class McpServer:
    def __init__(self, name, config, root):
        self.name, self.config, self.root = name, config, root
        self.process = None
        self.stderr = ""
        self._responses = queue.Queue()
        self._next_id = 0
        self._write_lock = threading.Lock()

    def start(self):
        command = [self.config["command"], *self.config.get("args", [])]
        cwd = self.config.get("cwd") or self.root
        if not os.path.isabs(cwd):
            cwd = os.path.join(self.root, cwd)
        environment = os.environ.copy()
        environment.update(self.config.get("env", {}))
        self.process = subprocess.Popen(
            command, cwd=cwd, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False,
        )
        threading.Thread(target=self._read_messages, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        answer = self.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "priya", "version": "1"},
        })
        if "error" in answer:
            self.close()
            raise RuntimeError(answer["error"])
        self.notify("notifications/initialized", {})

    def _read_stderr(self):
        try:
            for line in self.process.stderr:
                self.stderr = (self.stderr + line.decode("utf-8", "replace"))[-4000:]
        except Exception:
            pass

    def _read_messages(self):
        try:
            stream = self.process.stdout
            while True:
                headers = {}
                while True:
                    line = stream.readline()
                    if not line:
                        return
                    if line in (b"\r\n", b"\n"):
                        break
                    key, separator, value = line.decode("ascii", "replace").partition(":")
                    if separator:
                        headers[key.lower()] = value.strip()
                size = int(headers.get("content-length", "0"))
                if size:
                    message = json.loads(stream.read(size).decode("utf-8"))
                    if "id" in message:
                        self._responses.put(message)
        except Exception as error:
            self._responses.put({"id": None, "error": {"message": f"MCP reader failed: {error}"}})

    def _send(self, message):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("MCP server is not running")
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        frame = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        with self._write_lock:
            self.process.stdin.write(frame)
            self.process.stdin.flush()

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, params, timeout=20):
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline, deferred = time.monotonic() + timeout, []
        try:
            while time.monotonic() < deadline:
                try:
                    message = self._responses.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if message.get("id") == request_id:
                    if "error" in message:
                        error = message["error"]
                        return {"error": error.get("message", str(error)) if isinstance(error, dict) else str(error)}
                    return {"result": message.get("result", {})}
                deferred.append(message)
            detail = self.stderr.strip()
            return {"error": f"MCP request timed out: {method}" + (f" ({detail[-500:]})" if detail else "")}
        finally:
            for message in deferred:
                self._responses.put(message)

    def close(self):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=2)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        self.process = None


class McpManager:
    """Loads session MCP definitions and lists their *current* resource catalog."""
    def __init__(self, root, config=None):
        self.root = os.path.realpath(root)
        self._config = config
        self._servers = {}

    def reset(self, root=None):
        for server in self._servers.values():
            server.close()
        self._servers = {}
        if root is not None:
            self.root = os.path.realpath(root)

    def _definitions(self):
        if self._config is not None:
            raw = self._config
        else:
            raw = os.environ.get("PRIYA_MCP_SERVERS")
            if raw is None:
                config_path = os.path.join(self.root, ".priya", "mcp_servers.json")
                if not os.path.isfile(config_path):
                    return {}
                raw = Path(config_path).read_text(encoding="utf-8")
            if isinstance(raw, str):
                raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise ValueError("MCP server configuration must be a JSON object keyed by server name")
        definitions = {}
        for name, config in raw.items():
            if not isinstance(name, str) or not name or not isinstance(config, dict):
                raise ValueError("each MCP server needs a non-empty name and object configuration")
            command, args = config.get("command"), config.get("args", [])
            if not isinstance(command, str) or not command or not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ValueError(f"MCP server {name} requires command and optional string args")
            if not isinstance(config.get("env", {}), dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in config.get("env", {}).items()):
                raise ValueError(f"MCP server {name} env must map strings to strings")
            definitions[name] = config
        return definitions

    def _server(self, name, config):
        server = self._servers.get(name)
        if server is None:
            server = McpServer(name, config, self.root)
            server.start()
            self._servers[name] = server
        return server

    @staticmethod
    def _paged(server, method):
        items, cursor, seen = [], None, set()
        while True:
            params = {"cursor": cursor} if cursor else {}
            answer = server.request(method, params)
            if "error" in answer:
                return answer
            result = answer["result"] if isinstance(answer["result"], dict) else {}
            page = result.get("resources" if method == "resources/list" else "resourceTemplates", [])
            if not isinstance(page, list):
                return {"error": f"MCP server returned invalid {method} result"}
            items.extend(item for item in page if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor or cursor in seen:
                return {"result": items}
            seen.add(cursor)

    def list_resources(self):
        try:
            definitions = self._definitions()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            return {"error": f"invalid MCP server configuration: {error}"}
        resources, templates, servers = [], [], []
        for name, config in definitions.items():
            try:
                server = self._server(name, config)
                listed = self._paged(server, "resources/list")
                templated = self._paged(server, "resources/templates/list")
                server_view = {"name": name, "running": True}
                if "error" in listed:
                    server_view["error"] = listed["error"]
                else:
                    for resource in listed["result"]:
                        resources.append({"server": name, **resource})
                # Resource templates are optional in MCP. A server that does
                # not implement them still exposes its ordinary resources.
                if "error" not in templated:
                    for template in templated["result"]:
                        templates.append({"server": name, **template})
                servers.append(server_view)
            except (OSError, RuntimeError, ValueError) as error:
                servers.append({"name": name, "running": False, "error": str(error)})
        return {
            "servers": servers, "resources": resources, "resource_templates": templates,
            "count": len(resources), "template_count": len(templates),
        }
