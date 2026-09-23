#!/usr/bin/env python3
"""Versioned, local web artifacts for Priya.

Published HTML is stored beneath ``.priya/artifacts/<name>/versions``.  The
server exposes a stable artifact URL whose small wrapper refreshes its iframe
when a newer version is published, so an already-open browser updates in place.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
STORE = Path(os.environ.get("PRIYA_ARTIFACT_DIR", ROOT / ".priya" / "artifacts"))
SERVER_STATE = STORE / "server.json"
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def error(message):
    print(json.dumps({"error": message}))
    return 2


def artifact_dir(name):
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("name must use lowercase letters, numbers, '-' or '_'")
    return STORE / name


def metadata(name):
    path = artifact_dir(name) / "artifact.json"
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {name}")
    return json.loads(path.read_text())


def write_metadata(name, value):
    directory = artifact_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "artifact.json").write_text(json.dumps(value, indent=2))


def publish(name, title, html):
    directory = artifact_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    version = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    version_dir = directory / "versions" / version
    version_dir.mkdir(parents=True)
    (version_dir / "index.html").write_text(html)
    try:
        current = metadata(name)
    except FileNotFoundError:
        current = {"name": name, "created_at": time.time(), "versions": []}
    current["title"] = title or current.get("title") or name.replace("-", " ").title()
    current["latest"] = version
    current["updated_at"] = time.time()
    current["versions"].append({"id": version, "created_at": current["updated_at"]})
    write_metadata(name, current)
    return {"name": name, "title": current["title"], "version": version, "url": f"/artifact/{name}"}


def list_artifacts():
    if not STORE.exists():
        return []
    values = []
    for path in sorted(STORE.iterdir()):
        if path.is_dir() and (path / "artifact.json").exists():
            values.append(json.loads((path / "artifact.json").read_text()))
    return values


def history(name):
    value = metadata(name)
    return {"name": name, "title": value["title"], "latest": value["latest"], "versions": value["versions"]}


def revert(name, version):
    value = metadata(name)
    if version not in {item["id"] for item in value["versions"]}:
        raise ValueError(f"version not found: {version}")
    value["latest"] = version
    value["updated_at"] = time.time()
    write_metadata(name, value)
    return {"name": name, "version": version, "url": f"/artifact/{name}"}


def wrapper(name):
    value = metadata(name)
    latest = value["latest"]
    title = value["title"]
    return f"""<!doctype html><meta charset=utf-8><title>{title}</title>
<style>html,body,iframe{{margin:0;width:100%;height:100%;border:0}}body{{height:100vh}}</style>
<iframe id=artifact src=/content/{name}/{latest}/></iframe>
<script>
let version={json.dumps(latest)};
setInterval(async()=>{{const state=await fetch('/api/{name}',{{cache:'no-store'}}).then(r=>r.json());
if(state.latest!==version){{version=state.latest;document.getElementById('artifact').src='/content/{name}/'+version+'/';}}}},1000);
</script>""".encode()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/":
            rows = "".join(
                f'<li><a href="/artifact/{item["name"]}">{item["title"]}</a> <small>{item["latest"]}</small></li>'
                for item in list_artifacts()
            ) or "<li>No artifacts published yet.</li>"
            body = f"<!doctype html><title>Priya artifacts</title><h1>Priya artifacts</h1><ul>{rows}</ul>".encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        parts = path.strip("/").split("/")
        try:
            if len(parts) == 2 and parts[0] == "artifact":
                body = wrapper(parts[1])
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
            if len(parts) == 2 and parts[0] == "api":
                body = json.dumps(metadata(parts[1])).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
                return
        except (FileNotFoundError, ValueError):
            self.send_error(404); return
        if path.startswith("/content/"):
            # Public URLs omit the internal ``versions/`` storage directory.
            # /content/<name>/<version>/ maps to the corresponding revision.
            if len(parts) < 3:
                self.send_error(404); return
            target = (STORE / parts[1] / "versions" / parts[2]).resolve()
            if not target.is_relative_to(STORE.resolve()):
                self.send_error(403); return
            if target.is_dir():
                target /= "index.html"
            if target.is_file():
                body = target.read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
        self.send_error(404)


def serve(host, port):
    STORE.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(json.dumps({"url": f"http://{host}:{port}", "port": port}), flush=True)
    httpd.serve_forever()


def start(host, port):
    STORE.mkdir(parents=True, exist_ok=True)
    if SERVER_STATE.exists():
        state = json.loads(SERVER_STATE.read_text())
        try:
            os.kill(int(state["pid"]), 0)
            return {"url": state["url"], "port": state["port"], "already_running": True}
        except ProcessLookupError:
            SERVER_STATE.unlink()
    log = open(STORE / "server.log", "a")
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--host", host, "--port", str(port)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state = {"pid": process.pid, "host": host, "port": port, "url": f"http://{host}:{port}"}
    SERVER_STATE.write_text(json.dumps(state, indent=2))
    return state


def stop_server():
    if not SERVER_STATE.exists():
        return {"stopped": False, "reason": "not_running"}
    state = json.loads(SERVER_STATE.read_text())
    try:
        os.killpg(os.getpgid(int(state["pid"])), signal.SIGTERM)
    except ProcessLookupError:
        pass
    SERVER_STATE.unlink(missing_ok=True)
    return {"stopped": True}


def share():
    if not SERVER_STATE.exists():
        raise ValueError("start the artifact server before sharing it")
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise ValueError("cloudflared is not installed")
    state = json.loads(SERVER_STATE.read_text())
    log = open(STORE / "cloudflared.log", "a")
    process = subprocess.Popen([cloudflared, "tunnel", "--url", state["url"]], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"started": True, "pid": process.pid, "log": str(STORE / "cloudflared.log")}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    publish_parser = commands.add_parser("publish"); publish_parser.add_argument("name"); publish_parser.add_argument("--title", default=""); publish_parser.add_argument("--html", required=True)
    commands.add_parser("list")
    history_parser = commands.add_parser("history"); history_parser.add_argument("name")
    revert_parser = commands.add_parser("revert"); revert_parser.add_argument("name"); revert_parser.add_argument("version")
    start_parser = commands.add_parser("start"); start_parser.add_argument("--host", default="127.0.0.1"); start_parser.add_argument("--port", type=int, default=8765)
    serve_parser = commands.add_parser("serve"); serve_parser.add_argument("--host", default="127.0.0.1"); serve_parser.add_argument("--port", type=int, default=8765)
    commands.add_parser("stop"); commands.add_parser("share")
    args = parser.parse_args()
    try:
        if args.command == "publish": result = publish(args.name, args.title, args.html)
        elif args.command == "list": result = list_artifacts()
        elif args.command == "history": result = history(args.name)
        elif args.command == "revert": result = revert(args.name, args.version)
        elif args.command == "start": result = start(args.host, args.port)
        elif args.command == "serve": serve(args.host, args.port); return
        elif args.command == "stop": result = stop_server()
        else: result = share()
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(error(str(exc)))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
