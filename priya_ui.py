"""
Priya TUI — full-screen Textual app with a tree-shaped conversation log.

Each turn you send becomes a root node in the tree; tool calls made while
answering that turn appear as expandable/collapsible child nodes (pencil
icon, name(args), a diff-style +added/-removed stat when the tool result
looks like a diff, and the raw JSON result revealed on expand); the
model's streamed reply appears as a final text child under the same turn.

This is a genuine full-screen (alt-screen) app: Textual owns the viewport,
redraws the tree in place, and animates expand/collapse. Native terminal
scrollback no longer applies here — scrolling is handled by the app's own
scrollable tree view (mouse wheel / PageUp / j,k / arrow keys all work,
routed through Textual instead of the terminal).

Runs live_cli.py as a worker subprocess, using the same line-based
protocol as the old plain-stdout UI:
  <<TOOL_START>>{"name": ..., "detail": ...}
  <<TOOL_END>>{"name": ..., "result": ...}
  <<END>>
  (any other line = streamed model text)
"""

import os
import re
import sys
import json
import queue
import subprocess
import threading

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Tree, Input, Static
from textual.widgets.tree import TreeNode
from textual.reactive import reactive
from textual import work
from rich.text import Text

DIR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(DIR, "live_cli.py")
MODEL_NAME = "gemini-3.8-live"

SENTINEL = object()

PENCIL = "\u270e"
DOT = "\u25cf"
CHECK = "\u2713"
SPINNER_FRAMES = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
                   "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
THINKING_FRAMES = ["\u280b thinking\u2026", "\u2819 thinking\u2026", "\u2839 thinking\u2026",
                    "\u2838 thinking\u2026", "\u283c thinking\u2026", "\u2834 thinking\u2026",
                    "\u2826 thinking\u2026", "\u2827 thinking\u2026", "\u2807 thinking\u2026",
                    "\u280f thinking\u2026"]

_ADD_KEYS = ("added", "inserted", "lines_added", "additions")
_DEL_KEYS = ("removed", "deleted", "lines_removed", "deletions")


def diff_stat(result):
    """If a tool result carries diff-like counts, return (added, removed).
    Otherwise None. Looks for common key names; safe no-op otherwise."""
    if not isinstance(result, dict):
        return None
    added = next((result[k] for k in _ADD_KEYS if k in result), None)
    removed = next((result[k] for k in _DEL_KEYS if k in result), None)
    if added is None and removed is None:
        return None
    try:
        return int(added or 0), int(removed or 0)
    except (TypeError, ValueError):
        return None


def reader_thread(proc, q):
    for line in iter(proc.stdout.readline, ""):
        q.put(line)
    q.put(SENTINEL)


class ToolNodeData:
    """Attached to a tree node's .data for tool-call nodes."""
    def __init__(self, name, detail):
        self.name = name
        self.detail = detail
        self.result = None
        self.stat = None
        self.done = False


def tool_label(data, spinner_frame=None):
    label = Text()
    if data.done:
        label.append(f"{PENCIL} ", style="bold white")
    else:
        frame = spinner_frame or SPINNER_FRAMES[0]
        label.append(f"{frame} ", style="dim")
    name = (data.name[:1].upper() + data.name[1:]) if data.name else "Tool"
    label.append(f"{name}", style="bold white")
    label.append(f"({data.detail})", style="dim")
    if data.stat:
        added, removed = data.stat
        label.append("  ")
        if added:
            label.append(f"+{added} ", style="white")
        if removed:
            label.append(f"-{removed}", style="white")
    return label


class PriyaApp(App):
    CSS = """
    Screen {
        background: transparent;
    }
    #convo {
        height: 1fr;
        border: none;
        padding: 1 1 0 1;
        scrollbar-size: 1 1;
        scrollbar-color: transparent;
        scrollbar-color-hover: transparent;
        scrollbar-color-active: transparent;
        scrollbar-background: transparent;
        scrollbar-background-hover: transparent;
        scrollbar-background-active: transparent;
    }
    .bubble-row {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
    }
    .bubble {
        width: auto;
        max-width: 70%;
        padding: 0 1;
        border: none;
    }
    .user-bubble {
        background: #1b1b1b;
        margin-left: 2;
    }
    .ai-bubble {
        background: #111111;
        margin-left: 6;
    }
    .turn-tools {
        height: auto;
        border: none;
        margin: 0 0 1 6;
        padding: 0;
    }
    #inputbar {
        dock: bottom;
        height: 3;
        border: none;
        padding: 0 1;
    }
    #statusbar {
        dock: bottom;
        height: 1;
        color: white;
        padding: 0 1;
    }
    #keybar {
        dock: bottom;
        height: 1;
        color: white;
        background: transparent;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("ctrl+o", "toggle_last_tool", "Expand/collapse last tool call"),
        ("ctrl+e", "expand_all", "Expand all"),
        ("ctrl+r", "collapse_all", "Collapse all"),
        ("ctrl+c", "quit", "Quit"),
    ]

    busy = reactive(False)

    def __init__(self, talk=False):
        super().__init__()
        self.talk = talk
        self.proc = None
        self.out_q = queue.Queue()
        self.tool_nodes = []          # flat list of TreeNode, in call order
        self.spinner_i = 0
        self._spinner_timer = None
        self._thinking_bubble = None

    def compose(self) -> ComposeResult:
        convo = VerticalScroll(id="convo")
        yield convo
        yield Static(f"model: {MODEL_NAME}   cwd: {os.getcwd()}", id="statusbar")
        yield Input(placeholder="Type your message…  (Ctrl+O expands last tool call)",
                     id="inputbar")
        yield Static("^o expand/collapse   ^e expand all   ^r collapse all   ^c quit",
                      id="keybar")

    def on_mount(self):
        cmd = [sys.executable, WORKER] + (["--talk"] if self.talk else [])
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open("/tmp/priya_worker.log", "w"),
            text=True, bufsize=1,
        )
        threading.Thread(target=reader_thread, args=(self.proc, self.out_q),
                          daemon=True).start()
        self.query_one("#inputbar", Input).focus()
        self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self):
        if not self.busy:
            return
        self.spinner_i += 1
        refreshed = set()
        for node, tree in self.tool_nodes:
            data = node.data
            if data and not data.done:
                node.set_label(tool_label(data, SPINNER_FRAMES[self.spinner_i % len(SPINNER_FRAMES)]))
            if id(tree) not in refreshed:
                tree.refresh()
                refreshed.add(id(tree))
        if self._thinking_bubble is not None:
            frame = THINKING_FRAMES[self.spinner_i % len(THINKING_FRAMES)]
            self._thinking_bubble.update(frame)

    def action_toggle_last_tool(self):
        if self.tool_nodes:
            self.tool_nodes[-1][0].toggle()

    def action_expand_all(self):
        for node, tree in self.tool_nodes:
            tree.root.expand_all()

    def action_collapse_all(self):
        for node, tree in self.tool_nodes:
            node.collapse()

    def on_unmount(self):
        if self.proc is not None:
            self.proc.terminate()

    def show_thinking(self):
        convo = self.query_one("#convo", VerticalScroll)
        bubble = Static(THINKING_FRAMES[0], classes="bubble ai-bubble thinking-bubble")
        self._thinking_bubble = bubble
        convo.mount(bubble)
        convo.scroll_end(animate=False)

    def hide_thinking(self):
        if self._thinking_bubble is not None:
            self._thinking_bubble.remove()
            self._thinking_bubble = None

    def add_bubble(self, text, role):
        convo = self.query_one("#convo", VerticalScroll)
        bubble_cls = "user-bubble" if role == "user" else "ai-bubble"
        row = Vertical(classes="bubble-row")
        convo.mount(row)
        bubble = Static(text, classes=f"bubble {bubble_cls}")
        row.mount(bubble)
        convo.scroll_end(animate=False)
        return bubble

    def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.lower() == "q":
            self.exit()
            return
        self.run_turn(text)

    def _make_turn_tree(self):
        """Create a fresh inline Tree widget mounted into #convo for this turn."""
        convo = self.query_one("#convo", VerticalScroll)
        tree = Tree("", classes="turn-tools")
        tree.root.expand()
        tree.show_root = False
        tree.guide_depth = 3
        convo.mount(tree)
        return tree

    @work(exclusive=True, thread=True)
    def run_turn(self, text):
        self.call_from_thread(self.add_bubble, text, "user")
        self.busy = True
        self.call_from_thread(self.show_thinking)
        turn_tree = self.call_from_thread(self._make_turn_tree)

        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

        pending_node = None
        ai_text_parts = []
        ai_bubble = None

        def flush_ai_text():
            nonlocal ai_bubble
            if ai_text_parts:
                joined = " ".join(ai_text_parts).strip()
                if ai_bubble is None:
                    self.call_from_thread(self.hide_thinking)
                    ai_bubble = self.call_from_thread(self.add_bubble, joined, "ai")
                else:
                    self.call_from_thread(ai_bubble.update, joined)
                    convo = self.query_one("#convo", VerticalScroll)
                    self.call_from_thread(convo.scroll_end, animate=False)

        while True:
            item = self.out_q.get()
            if item is SENTINEL:
                self.busy = False
                self.call_from_thread(self.add_bubble, "(worker closed the connection)", "ai")
                return

            line = item.strip()

            if line.startswith("<<TOOL_START>>"):
                try:
                    payload = json.loads(line[len("<<TOOL_START>>"):])
                    name, detail = payload["name"], payload["detail"]
                except (json.JSONDecodeError, KeyError):
                    name, detail = "tool", "(unparsed)"
                data = ToolNodeData(name, detail)

                def add_tool_node(d=data, tt=turn_tree):
                    n = tt.root.add(tool_label(d), data=d)
                    self.tool_nodes.append((n, tt))
                    return n

                pending_node = self.call_from_thread(add_tool_node)
                continue

            if line.startswith("<<TOOL_END>>"):
                try:
                    payload = json.loads(line[len("<<TOOL_END>>"):])
                    name, result = payload["name"], payload["result"]
                    result_text = json.dumps(result, indent=2)
                except (json.JSONDecodeError, KeyError):
                    result = None
                    result_text = "(unparsed result)"
                if pending_node is not None:
                    data = pending_node.data
                    data.done = True
                    data.result = result_text
                    data.stat = diff_stat(result)

                    def finish_tool_node(n=pending_node, d=data, rt=result_text, tt=turn_tree):
                        n.set_label(tool_label(d))
                        n.remove_children()
                        for ln in (rt.splitlines() or [""]):
                            n.add_leaf(Text(ln, style="dim"))
                        tt.refresh()

                    self.call_from_thread(finish_tool_node)
                pending_node = None
                continue

            if line == "<<END>>":
                flush_ai_text()
                self.busy = False
                return

            if line:
                ai_text_parts.append(line)
                flush_ai_text()


def main():
    talk = "--talk" in sys.argv[1:]
    app = PriyaApp(talk=talk)
    app.run()


if __name__ == "__main__":
    main()
