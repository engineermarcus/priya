"""Small, dependency-free stdio client for Language Server Protocol servers.

The manager deliberately owns long-lived server processes.  It keeps documents
open and sends full-text ``didChange`` notifications when their on-disk content
changes, which makes semantic queries reflect edits made during a Priya session.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerSpec:
    language: str
    language_id: str
    command: tuple[str, ...]


_SPECS = {
    ".py": ("python", "python", (("pyright-langserver", "--stdio"), ("pylsp",))),
    ".js": ("javascript", "javascript", (("typescript-language-server", "--stdio"),)),
    ".jsx": ("javascript", "javascriptreact", (("typescript-language-server", "--stdio"),)),
    ".ts": ("typescript", "typescript", (("typescript-language-server", "--stdio"),)),
    ".tsx": ("typescript", "typescriptreact", (("typescript-language-server", "--stdio"),)),
    ".go": ("go", "go", (("gopls", "serve"),)),
    ".rs": ("rust", "rust", (("rust-analyzer",),)),
}


def _uri_to_path(uri):
    if not isinstance(uri, str) or not uri.startswith("file://"):
        return uri
    try:
        from urllib.parse import unquote, urlparse
        return os.path.realpath(unquote(urlparse(uri).path))
    except Exception:
        return uri


def _location(value):
    """Convert an LSP Location to a compact, user-facing path/range mapping."""
    if not isinstance(value, dict):
        return value
    # WorkspaceSymbol wraps its location one level deeper than Location.
    location = value.get("location") if isinstance(value.get("location"), dict) else value
    uri = location.get("uri") or location.get("targetUri")
    range_ = location.get("range") or location.get("targetSelectionRange")
    if not isinstance(range_, dict):
        return value
    start, end = range_.get("start", {}), range_.get("end", {})
    if not isinstance(start, dict) or not isinstance(end, dict):
        return value
    return {
        "path": _uri_to_path(uri),
        "range": {
            "start": {"line": start.get("line", 0) + 1, "character": start.get("character", 0)},
            "end": {"line": end.get("line", 0) + 1, "character": end.get("character", 0)},
        },
    }


class LanguageServer:
    def __init__(self, spec: ServerSpec, root: str):
        self.spec = spec
        self.root = root
        self.process = None
        self._responses = queue.Queue()
        self._next_id = 0
        self._write_lock = threading.Lock()
        self._documents = {}
        self.diagnostics = {}
        self.stderr = ""

    def start(self):
        self.process = subprocess.Popen(
            self.spec.command, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=False,
        )
        threading.Thread(target=self._read_messages, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        result = self.request("initialize", {
            "processId": os.getpid(), "rootUri": Path(self.root).as_uri(),
            "workspaceFolders": [{"uri": Path(self.root).as_uri(), "name": os.path.basename(self.root)}],
            "capabilities": {"textDocument": {"definition": {"linkSupport": True}}},
        })
        if "error" in result:
            self.close()
            raise RuntimeError(result["error"])
        self.notify("initialized", {})

    def _read_stderr(self):
        try:
            for raw in self.process.stderr:
                self.stderr = (self.stderr + raw.decode("utf-8", "replace"))[-4000:]
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
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                message = json.loads(stream.read(length).decode("utf-8"))
                if message.get("method") == "textDocument/publishDiagnostics":
                    params = message.get("params", {})
                    self.diagnostics[params.get("uri")] = params.get("diagnostics", [])
                elif "id" in message:
                    self._responses.put(message)
        except Exception as error:
            self._responses.put({"id": None, "error": {"message": f"LSP reader failed: {error}"}})

    def _send(self, message):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("language server is not running")
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
        deadline = time.monotonic() + timeout
        deferred = []
        try:
            while time.monotonic() < deadline:
                try:
                    message = self._responses.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if message.get("id") == request_id:
                    if "error" in message:
                        error = message["error"]
                        return {"error": error.get("message", str(error)) if isinstance(error, dict) else str(error)}
                    return {"result": message.get("result")}
                deferred.append(message)
            detail = self.stderr.strip()
            return {"error": f"LSP request timed out: {method}" + (f" ({detail[-500:]})" if detail else "")}
        finally:
            for message in deferred:
                self._responses.put(message)

    def sync(self, path):
        text = Path(path).read_text(encoding="utf-8")
        uri = Path(path).as_uri()
        old = self._documents.get(uri)
        if old is None:
            self._documents[uri] = (1, text)
            self.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": self.spec.language_id, "version": 1, "text": text,
            }})
        elif old[1] != text:
            version = old[0] + 1
            self._documents[uri] = (version, text)
            self.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": version}, "contentChanges": [{"text": text}]})
        return uri

    def close(self):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                self.request("shutdown", {}, timeout=2)
                self.notify("exit", {})
                self.process.wait(timeout=2)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        self.process = None


class LspManager:
    """Starts at most one server per language for the active workspace."""
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self._servers = {}

    def reset(self, root=None):
        for server in self._servers.values():
            server.close()
        self._servers = {}
        if root is not None:
            self.root = os.path.realpath(root)

    def _spec_for(self, path):
        suffix = Path(path).suffix.lower()
        item = _SPECS.get(suffix)
        if item is None:
            raise ValueError(f"no LSP language server is configured for {suffix or 'files without an extension'}")
        language, language_id, candidates = item
        command = next((candidate for candidate in candidates if shutil.which(candidate[0])), None)
        if command is None:
            names = ", ".join(candidate[0] for candidate in candidates)
            raise ValueError(f"no {language} language server found (install one of: {names})")
        return ServerSpec(language, language_id, command)

    def _server_for(self, path):
        spec = self._spec_for(path)
        server = self._servers.get(spec.language)
        if server is None:
            server = LanguageServer(spec, self.root)
            server.start()
            self._servers[spec.language] = server
        return server

    @staticmethod
    def _position(args):
        line, character = args.get("line"), args.get("character", 0)
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ValueError("LSP requires a one-based integer line")
        if not isinstance(character, int) or isinstance(character, bool) or character < 0:
            raise ValueError("LSP character must be a zero-based non-negative integer")
        return {"line": line - 1, "character": character}

    def query(self, args, resolve_path):
        action = args.get("action")
        if action == "status":
            return {"root": self.root, "servers": [{"language": key, "command": list(value.spec.command), "running": value.process is not None and value.process.poll() is None} for key, value in self._servers.items()]}
        if action == "workspace_symbols":
            query = args.get("query", "")
            if not isinstance(query, str):
                return {"error": "LSP query must be a string"}
            # A workspace query needs a language anchor because LSP servers are language-specific.
            path = resolve_path(args.get("path"))
            server = self._server_for(path)
            answer = server.request("workspace/symbol", {"query": query})
            return self._format(answer, action)
        path = resolve_path(args.get("path"))
        server = self._server_for(path)
        uri = server.sync(path)
        if action == "definition":
            answer = server.request("textDocument/definition", {"textDocument": {"uri": uri}, "position": self._position(args)})
        elif action == "references":
            answer = server.request("textDocument/references", {"textDocument": {"uri": uri}, "position": self._position(args), "context": {"includeDeclaration": args.get("include_declaration", True)}})
        elif action == "hover":
            answer = server.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": self._position(args)})
        elif action == "document_symbols":
            answer = server.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        elif action == "diagnostics":
            answer = {"result": server.diagnostics.get(uri, [])}
        else:
            return {"error": "LSP action must be status, definition, references, hover, document_symbols, workspace_symbols, or diagnostics"}
        return self._format(answer, action)

    def _format(self, answer, action):
        if "error" in answer:
            return answer
        result = answer.get("result")
        if action in ("definition", "references", "workspace_symbols"):
            items = result if isinstance(result, list) else ([] if result is None else [result])
            return {"results": [_location(item) for item in items]}
        if action == "diagnostics":
            return {"diagnostics": result or []}
        return {"result": result}
