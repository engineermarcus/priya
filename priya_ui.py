"""
Priya CLI — full-screen prompt_toolkit UI with collapsible tool-call
output. Runs live_cli.py as a worker subprocess.

Layout: scrollback pane (top, fills space) + single-line input box
(bottom). Tool calls render as a one-line summary; Ctrl+O toggles
full detail (command/task + result) for the most recent tool call.
"""

import os
import sys
import json
import queue
import subprocess
import threading

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.styles import Style
from prompt_toolkit.layout.margins import ScrollbarMargin

DIR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(DIR, "live_cli.py")
MODEL_NAME = "gemini-3.8-live"

SENTINEL = object()

style = Style.from_dict({
    "prompt": "fg:#ff5555 bold",
    "dim": "fg:#666666",
    "tool": "fg:#d7af00",
    "tool.expanded": "fg:#d7af00 bold",
    "assistant": "fg:#00afaf",
    "header": "fg:#00afaf bold",
    "status-bar": "bg:#303030 fg:#aaaaaa",
})


class ToolEntry:
    def __init__(self, name, detail_line):
        self.name = name
        self.detail_line = detail_line
        self.result = None
        self.expanded = False

    def render(self):
        arrow = "v" if self.expanded else ">"
        head = f"  [{arrow}] {self.name}: {self.detail_line}"
        lines = [("class:tool", head + "\n")]
        if self.expanded:
            body = self.result if self.result is not None else "(running...)"
            for line in body.splitlines() or [""]:
                lines.append(("class:dim", f"        {line}\n"))
        return lines


class ChatState:
    def __init__(self):
        self.lines = []
        self.tool_entries = []

    def add_text(self, style_class, text):
        self.lines.append((style_class, text))

    def start_tool(self, name, detail_line):
        entry = ToolEntry(name, detail_line)
        self.tool_entries.append(entry)
        self.lines.append(("tool_ref", entry))
        return entry

    def last_tool_entry(self):
        return self.tool_entries[-1] if self.tool_entries else None

    def render_fragments(self):
        frags = []
        for item in self.lines:
            if item[0] == "tool_ref":
                frags.extend(item[1].render())
            else:
                style_class, text = item
                frags.append((f"class:{style_class}", text))
        return frags


def reader_thread(proc, q):
    for line in iter(proc.stdout.readline, ""):
        q.put(line)
    q.put(SENTINEL)


def main():
    talk = "--talk" in sys.argv[1:]
    cmd = [sys.executable, WORKER] + (["--talk"] if talk else [])
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=open("/tmp/priya_worker.log", "w"),
        text=True, bufsize=1,
    )

    out_q = queue.Queue()
    threading.Thread(target=reader_thread, args=(proc, out_q), daemon=True).start()

    state = ChatState()
    state.add_text("header", f"Priya · {MODEL_NAME}\n")
    state.add_text("dim", f"{os.getcwd()}\n\n")

    input_buffer = Buffer(multiline=False)
    status_text = ["ready"]

    output_control = FormattedTextControl(lambda: state.render_fragments())
    output_window = Window(
        content=output_control,
        wrap_lines=True,
        right_margins=[ScrollbarMargin(display_arrows=True)],
        always_hide_cursor=True,
    )

    input_window = Window(
        content=BufferControl(buffer=input_buffer),
        height=1,
        style="class:prompt",
    )

    status_window = Window(
        content=FormattedTextControl(lambda: [("class:status-bar", f" {status_text[0]}  |  Ctrl+O expand/collapse last tool  |  Ctrl+C quit ")]),
        height=1,
        style="class:status-bar",
    )

    root = HSplit([
        output_window,
        Window(height=1, char="-", style="class:dim"),
        input_window,
        status_window,
    ])

    kb = KeyBindings()

    @kb.add("c-o")
    def _(event):
        entry = state.last_tool_entry()
        if entry is not None:
            entry.expanded = not entry.expanded
        event.app.invalidate()

    @kb.add("c-c")
    def _(event):
        event.app.exit()

    @kb.add("enter")
    def _(event):
        text = input_buffer.text
        input_buffer.text = ""
        if not text.strip():
            return
        if text.strip().lower() == "q":
            event.app.exit()
            return
        state.add_text("prompt", f"> {text}\n")
        proc.stdin.write(text + "\n")
        proc.stdin.flush()
        status_text[0] = "thinking..."
        event.app.invalidate()

    app = Application(layout=Layout(root, focused_element=input_window), key_bindings=kb, style=style, full_screen=True)

    def poll_worker():
        pending_tool = None
        while True:
            item = out_q.get()
            if item is SENTINEL:
                status_text[0] = "worker closed the connection"
                app.invalidate()
                break

            line = item.strip()

            if line.startswith("<<TOOL_START>>"):
                try:
                    payload = json.loads(line[len("<<TOOL_START>>"):])
                    name, detail = payload["name"], payload["detail"]
                except (json.JSONDecodeError, KeyError):
                    name, detail = "tool", "(unparsed)"
                pending_tool = state.start_tool(name, detail)
                status_text[0] = f"running {name}..."
                app.invalidate()
                continue

            if line.startswith("<<TOOL_END>>"):
                try:
                    payload = json.loads(line[len("<<TOOL_END>>"):])
                    name, result = payload["name"], payload["result"]
                    result_text = json.dumps(result, indent=2)
                except (json.JSONDecodeError, KeyError):
                    name, result_text = "tool", "(unparsed result)"
                if pending_tool is not None and pending_tool.name == name:
                    pending_tool.result = result_text
                pending_tool = None
                status_text[0] = "thinking..."
                app.invalidate()
                continue

            if line == "<<END>>":
                status_text[0] = "ready"
                state.add_text("assistant", "\n")
                app.invalidate()
                continue

            if line:
                state.add_text("assistant", line + " ")
                app.invalidate()

    threading.Thread(target=poll_worker, daemon=True).start()

    try:
        app.run()
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
