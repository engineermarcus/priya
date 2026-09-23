"""
Priya CLI — full-screen prompt_toolkit UI with collapsible tool-call
output. Runs live_cli.py as a worker subprocess.

Flow: you type at the "> " prompt -> an inline spinner appears in the
scrollback exactly where the reply will land -> it's replaced in place
by tool calls (rendered as Name(args) with a dropdown arrow revealing
grayed output) and/or the model's streamed text. No speaker labels —
your line and the model's line are told apart by color/weight only,
and ">" only ever appears in the input line, never in the transcript.
"""

import os
import sys
import json
import queue
import subprocess
import threading
import time

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.styles import Style

DIR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(DIR, "live_cli.py")
MODEL_NAME = "gemini-3.8-live-extended-thinking"

SENTINEL = object()

BANNER = r"""
 ____   ____  _____  __     __ _
|  _ \ |  _ \|_ _\ \/ /   /\  \ \
| |_) || |_) || | \  /   /  \  \ \
|  __/ |  _ < | | /  \  / /\ \  \ \
|_|    |_| \_\___/_/\_\/_/  \_\  \_\
""".strip("\n")

SPINNER_FRAMES = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
                   "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]

style = Style.from_dict({
    "banner": "fg:#00afaf bold",
    "dim": "fg:#666666",
    "rule": "fg:#3a3a3a",

    "text.you": "fg:#ffffff bold",
    "text.ai": "",  # plain default terminal foreground — no arbitrary tint
    "text.frozen": "fg:#555555 italic",

    "input.idle": "fg:#ffffff",
    "input.busy": "fg:#666666",

    "spinner": "fg:#666666 italic",

    "tool.name": "fg:#d7af00 bold",
    "tool.arrow": "fg:#ffd75f bold",  # brighter + bold — this is the click/expand affordance
    "tool.hint": "fg:#888888 italic",
    "tool.dropdown": "fg:#555555",
    "tool.output": "fg:#7a7a7a",

    "prompt-gutter": "fg:#ff5555 bold",
    "status-bar": "fg:#666666",
})


class SpinnerEntry:
    def __init__(self):
        self.frame = 0
        self.alive = True

    def render(self):
        f = SPINNER_FRAMES[self.frame % len(SPINNER_FRAMES)]
        return [("class:spinner", f"{f} thinking\n")]


class ToolEntry:
    def __init__(self, name, detail_line):
        # "bash" -> "Bash", "read_file" -> "Read_file" — first letter up.
        self.label = (name[:1].upper() + name[1:]) if name else "Tool"
        self.detail_line = detail_line
        self.result = None
        self.expanded = False

    def render(self):
        if self.expanded:
            arrow, hint = "\u25be", "show less"  # ▾
        else:
            arrow, hint = "\u25b8", "show more"  # ▸
        lines = [
            ("class:tool.arrow", f"{arrow} "),
            ("class:tool.name", f"{self.label}({self.detail_line})"),
            ("class:tool.hint", f"  [{hint}]\n"),
        ]
        if self.expanded:
            body = self.result if self.result is not None else "running..."
            lines.append(("class:tool.dropdown", "  \u2514\u2500\n"))  # └─
            for line in body.splitlines() or [""]:
                lines.append(("class:tool.output", f"     {line}\n"))
        return lines


class ChatState:
    def __init__(self):
        self.entries = []  # list of ("static", frags) | ("tool", ToolEntry) | ("spinner", SpinnerEntry)

    def add_static(self, frags):
        self.entries.append(("static", frags))

    def start_tool(self, name, detail_line):
        entry = ToolEntry(name, detail_line)
        self.entries.append(("tool", entry))
        return entry

    def start_spinner(self):
        entry = SpinnerEntry()
        self.entries.append(("spinner", entry))
        return entry

    def drop_spinner(self):
        if self.entries and self.entries[-1][0] == "spinner":
            self.entries.pop()

    def last_of_kind(self, kind):
        for k, e in reversed(self.entries):
            if k == kind:
                return e
        return None

    def render_fragments(self):
        frags = []
        for kind, payload in self.entries:
            if kind == "static":
                frags.extend(payload)
            else:
                frags.extend(payload.render())
        return frags


def reader_thread(proc, q):
    for line in iter(proc.stdout.readline, ""):
        q.put(line)
    q.put(SENTINEL)


def spinner_thread(app, state):
    while True:
        entry = state.last_of_kind("spinner")
        if entry is not None and entry.alive:
            entry.frame += 1
            app.invalidate()
        time.sleep(0.1)


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
    state.add_static([("class:banner", BANNER + "\n")])
    state.add_static([("class:dim", f"  model: {MODEL_NAME}\n  cwd:   {os.getcwd()}\n")])
    state.add_static([("class:rule", "  " + "-" * 60 + "\n\n")])

    input_buffer = Buffer(multiline=False)
    status_text = ["ready"]

    follow_bottom = [True]  # auto-scroll to newest content unless user paged up
    app_ref = {}  # filled in once Application exists; refresh() closes over it

    output_control = FormattedTextControl(lambda: state.render_fragments())
    output_window = Window(
        content=output_control,
        wrap_lines=True,
        always_hide_cursor=True,
        allow_scroll_beyond_bottom=True,
    )

    def refresh():
        # Any time content changes, keep the view pinned to the newest
        # line unless the user has manually scrolled up. A very large
        # scroll value is clamped to the real max by prompt_toolkit's
        # own renderer, so this is a cheap way to say "show the end".
        if follow_bottom[0]:
            output_window.vertical_scroll = 1 << 30
        if "app" in app_ref:
            app_ref["app"].invalidate()

    busy = [False]  # True from send until <<END>> (or an Esc interrupt)

    def input_style():
        return "class:input.busy" if busy[0] else "class:input.idle"

    # ">" lives here, and only here — get_line_prefix renders it as part
    # of the input line itself, never written into the transcript.
    # style grays out while busy[0] is True, per input_style() above.
    input_window = Window(
        content=BufferControl(buffer=input_buffer),
        height=1,
        get_line_prefix=lambda line_number, wrap_count: [("class:prompt-gutter", "> ")],
        style=input_style,
    )

    status_window = Window(
        content=FormattedTextControl(
            lambda: [("class:status-bar", f"  {status_text[0]:<20} Ctrl+O expand/collapse   Ctrl+S save transcript   Ctrl+C quit")]
        ),
        height=1,
    )

    root = HSplit([
        output_window,
        Window(height=1, char="\u2500", style="class:rule"),
        input_window,
        status_window,
    ])

    kb = KeyBindings()

    @kb.add("c-o")
    def _(event):
        entry = state.last_of_kind("tool")
        if entry is not None:
            entry.expanded = not entry.expanded
        event.app.invalidate()

    @kb.add("c-s")
    def _(event):
        # Plain-text dump (no style/color codes) for pasting elsewhere —
        # click-drag selection is unreliable now that mouse_support=True
        # has the terminal handing mouse events to the app.
        path = "/tmp/priya_transcript.txt"
        try:
            text = "".join(frag_text for _style, frag_text in state.render_fragments())
            with open(path, "w") as f:
                f.write(text)
            status_text[0] = f"saved transcript -> {path}"
        except Exception as e:
            status_text[0] = f"transcript save failed: {e}"
        event.app.invalidate()

    @kb.add("c-c")
    def _(event):
        event.app.exit()

    def _scroll(delta):
        # output_window is not focused (input always is), so it doesn't
        # get key events by default — drive its scroll position directly.
        follow_bottom[0] = False  # manual scroll breaks auto-follow
        new_pos = max(0, output_window.vertical_scroll + delta)
        output_window.vertical_scroll = new_pos

    @kb.add("pageup")
    def _(event):
        _scroll(-10)
        event.app.invalidate()

    @kb.add("pagedown")
    def _(event):
        _scroll(10)
        event.app.invalidate()

    @kb.add("c-u")
    def _(event):
        _scroll(-3)
        event.app.invalidate()

    @kb.add("c-d")
    def _(event):
        _scroll(3)
        event.app.invalidate()

    @kb.add("enter")
    def _(event):
        text = input_buffer.text
        input_buffer.text = ""
        if not text.strip():
            return
        if text.strip().lower() == "q":
            event.app.exit()
            return

        if busy[0]:
            # Agent is still responding — don't send. Clear it from the
            # input but show it "frozen" (grayed) in the transcript so
            # it's clear it was blocked, not silently dropped.
            state.add_static([("class:text.frozen", text + "  (not sent — press Esc to interrupt first)\n\n")])
            follow_bottom[0] = True
            refresh()
            return

        busy[0] = True
        follow_bottom[0] = True
        state.add_static([("class:text.you", text + "\n\n")])
        state.start_spinner()
        proc.stdin.write(text + "\n")
        proc.stdin.flush()
        status_text[0] = "thinking..."
        refresh()

    @kb.add("escape")
    def _(event):
        if not busy[0]:
            return
        # Best-effort interrupt. live_cli.py's actual cancellation
        # protocol is unknown to this file — if it expects something
        # other than a "<<CANCEL>>" line (a signal, a different
        # sentinel, etc.), tell me and I'll wire it to match.
        try:
            proc.stdin.write("<<CANCEL>>\n")
            proc.stdin.flush()
        except Exception:
            pass
        state.drop_spinner()
        state.add_static([("class:dim", "(interrupted)\n\n")])
        busy[0] = False
        status_text[0] = "ready"
        follow_bottom[0] = True
        refresh()

    app = Application(
        layout=Layout(root, focused_element=input_window),
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=True,  # lets the terminal's mouse wheel scroll output_window
    )
    app_ref["app"] = app
    threading.Thread(target=spinner_thread, args=(app, state), daemon=True).start()

    def poll_worker():
        pending_tool = None
        ai_line_open = False

        def close_ai_line():
            nonlocal ai_line_open
            if ai_line_open:
                state.add_static([("class:text.ai", "\n\n")])
                ai_line_open = False

        while True:
            item = out_q.get()
            if item is SENTINEL:
                status_text[0] = "worker closed the connection"
                busy[0] = False
                refresh()
                break

            line = item.strip()

            if line.startswith("<<TOOL_START>>"):
                state.drop_spinner()
                try:
                    payload = json.loads(line[len("<<TOOL_START>>"):])
                    name, detail = payload["name"], payload["detail"]
                except (json.JSONDecodeError, KeyError):
                    name, detail = "tool", "(unparsed)"
                pending_tool = state.start_tool(name, detail)
                status_text[0] = f"running {pending_tool.label}..."
                refresh()
                continue

            if line.startswith("<<TOOL_END>>"):
                try:
                    payload = json.loads(line[len("<<TOOL_END>>"):])
                    name, result = payload["name"], payload["result"]
                    result_text = json.dumps(result, indent=2)
                except (json.JSONDecodeError, KeyError):
                    result_text = "(unparsed result)"
                if pending_tool is not None:
                    pending_tool.result = result_text
                pending_tool = None
                status_text[0] = "thinking..."
                refresh()
                continue

            if line == "<<END>>":
                state.drop_spinner()
                close_ai_line()
                busy[0] = False
                status_text[0] = "ready"
                refresh()
                continue

            if line:
                state.drop_spinner()
                if not ai_line_open:
                    ai_line_open = True
                state.add_static([("class:text.ai", line + " ")])
                refresh()

    threading.Thread(target=poll_worker, daemon=True).start()

    try:
        app.run()
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
