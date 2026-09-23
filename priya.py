"""
Priya TUI — redesigned, bug-fixed.

Critical fixes vs original:
  1. Worker thread NEVER blocks on UI. All communication is
     fire-and-forget via ui_q. No call_from_thread. No result_q.
  2. Thinking bubble hides the instant first text arrives,
     not on EndTurn.
  3. Tool nodes tracked by integer ID (not object references).
     Multiple tool calls per turn work correctly.
  4. AI text streams into a pre-mounted bubble. No mid-stream
     widget creation.
  5. UI updates batched at 30fps via set_interval.

Protocol (same as original):
  <<TOOL_START>>{"name": ..., "detail": ...}
  <<TOOL_END>>{"name": ..., "result": ...}
  <<END>>
  (any other line = streamed model text)
"""

import os
import sys
import json
import queue
import subprocess
import threading

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Tree, Input, Static
from textual.reactive import reactive
from textual import work
from rich.text import Text

DIR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(DIR, "live_cli.py")
MODEL_NAME = "gemini-3.8-live"

SENTINEL = object()

PENCIL = "\u270e"
CHECK = "\u2713"
SPINNER_FRAMES = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
                   "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
THINKING_FRAMES = [f + " thinking\u2026" for f in SPINNER_FRAMES]

_ADD_KEYS = ("added", "inserted", "lines_added", "additions")
_DEL_KEYS = ("removed", "deleted", "lines_removed", "deletions")


def diff_stat(result):
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
    label.append(name, style="bold white")
    label.append(f"({data.detail})", style="dim")
    if data.stat:
        added, removed = data.stat
        label.append("  ")
        if added:
            label.append(f"+{added} ", style="white")
        if removed:
            label.append(f"-{removed}", style="white")
    return label


# ─────────────────────────────────────────────────────────────────────────────
# Fire-and-forget messages: worker thread → UI thread
# No result_q. No blocking. Ever.
# ─────────────────────────────────────────────────────────────────────────────

class Msg:
    pass

class MountTurn(Msg):
    def __init__(self, user_text):
        self.user_text = user_text

class AddToolNode(Msg):
    def __init__(self, tool_id, name, detail):
        self.tool_id = tool_id
        self.name = name
        self.detail = detail

class FinishToolNode(Msg):
    def __init__(self, tool_id, result_text, stat):
        self.tool_id = tool_id
        self.result_text = result_text
        self.stat = stat

class AppendText(Msg):
    def __init__(self, text):
        self.text = text

class HideThinking(Msg):
    pass

class EndTurn(Msg):
    pass

class WorkerClosed(Msg):
    pass


class PriyaApp(App):
    CSS = """
    Screen {
        background: #0a0a0a;
    }

    #convo {
        height: 1fr;
        padding: 1 2 0 2;
        scrollbar-size: 1 1;
        scrollbar-color: #333;
        scrollbar-background: transparent;
    }

    .turn {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        padding: 0;
    }

    .bubble {
        width: auto;
        max-width: 72%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    .user-bubble {
        background: #1b1b1b;
        margin-left: 0;
    }
    .ai-bubble {
        background: #111111;
        margin-left: 2;
    }
    .thinking {
        color: #555555;
        background: transparent;
    }

    .turn-tools {
        width: 100%;
        height: auto;
        margin: 0 0 1 2;
        padding: 0;
        border: none;
    }
    .turn-tools Tree {
        background: transparent;
    }
    .turn-tools .tree--label {
        color: white;
    }
    .turn-tools .tree--guides {
        color: #333;
    }
    .turn-tools .tree--guides-hover {
        color: #555;
    }

    #statusbar {
        dock: bottom;
        height: 1;
        background: #111;
        color: #666;
        padding: 0 2;
    }
    #keybar {
        dock: bottom;
        height: 1;
        background: #111;
        color: #555;
        padding: 0 2;
    }
    #inputbar {
        dock: bottom;
        height: 3;
        border: none;
        padding: 0 2;
        background: #0a0a0a;
    }
    #inputbar Input {
        border: none;
    }
    #inputbar Input:focus {
        border: none;
    }
    """

    BINDINGS = [
        ("ctrl+o", "toggle_last_tool", "Expand/collapse last tool"),
        ("ctrl+e", "expand_all", "Expand all"),
        ("ctrl+r", "collapse_all", "Collapse all"),
        ("ctrl+c", "quit", "Quit"),
    ]

    busy = reactive(False)

    def __init__(self, talk=False):
        super().__init__()
        self.talk = talk
        self.proc = None
        self.raw_q = queue.Queue()
        self.ui_q = queue.Queue()
        self._spinner_i = 0
        self._flush_timer = None
        self._spinner_timer = None
        # Current turn state (UI thread only)
        self._cur_tree = None
        self._cur_ai_bubble = None
        self._cur_ai_text = ""
        self._cur_thinking = None
        self._cur_tool_node = None
        self._all_tool_nodes = []
        self._node_registry = {}

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="convo")
        yield Static(f"  {MODEL_NAME}  \u00b7  {os.getcwd()}", id="statusbar")
        yield Input(placeholder="Type your message\u2026", id="inputbar")
        yield Static("^o expand  ^e all  ^r collapse  ^c quit", id="keybar")

    def on_mount(self):
        cmd = [sys.executable, WORKER] + (["--talk"] if self.talk else [])
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open("/tmp/priya_worker.log", "w"),
            text=True, bufsize=1,
        )
        threading.Thread(target=reader_thread, args=(self.proc, self.raw_q),
                         daemon=True).start()
        self.query_one("#inputbar", Input).focus()
        self._flush_timer = self.set_interval(1 / 30, self._drain_ui_q)
        self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

    def on_unmount(self):
        if self.proc is not None:
            self.proc.terminate()

    def _set_status(self, text, color="#666"):
        bar = self.query_one("#statusbar", Static)
        bar.styles.color = color
        bar.update(text)

    def _tick_spinner(self):
        if not self.busy:
            return
        self._spinner_i += 1
        frame = SPINNER_FRAMES[self._spinner_i % len(SPINNER_FRAMES)]
        self._set_status(f"  {frame}  {MODEL_NAME} is working\u2026")
        if self._cur_thinking is not None:
            tf = THINKING_FRAMES[self._spinner_i % len(THINKING_FRAMES)]
            self._cur_thinking.update(tf)
        if self._cur_tool_node is not None and not self._cur_tool_node.data.done:
            self._cur_tool_node.set_label(
                tool_label(self._cur_tool_node.data, frame)
            )

    # ── UI queue drain ───────────────────────────────────────────────────────

    def _drain_ui_q(self):
        try:
            while True:
                msg = self.ui_q.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass

    def _handle_msg(self, msg):
        if isinstance(msg, MountTurn):
            self._do_mount_turn(msg.user_text)
        elif isinstance(msg, AddToolNode):
            self._do_add_tool_node(msg.tool_id, msg.name, msg.detail)
        elif isinstance(msg, FinishToolNode):
            self._do_finish_tool_node(msg.tool_id, msg.result_text, msg.stat)
        elif isinstance(msg, AppendText):
            self._do_append_text(msg.text)
        elif isinstance(msg, HideThinking):
            self._do_hide_thinking()
        elif isinstance(msg, EndTurn):
            self._do_end_turn()
        elif isinstance(msg, WorkerClosed):
            self._do_worker_closed()

    # ── UI operations (UI thread only, called from _drain_ui_q) ─────────────

    def _do_mount_turn(self, user_text):
        convo = self.query_one("#convo", VerticalScroll)
        turn = Vertical(classes="turn")
        convo.mount(turn)

        user_bubble = Static(user_text, classes="bubble user-bubble")
        turn.mount(user_bubble)

        tree = Tree("", classes="turn-tools")
        tree.root.expand()
        tree.show_root = False
        tree.guide_depth = 2
        turn.mount(tree)

        ai_bubble = Static("", classes="bubble ai-bubble")
        turn.mount(ai_bubble)

        thinking_bubble = Static(THINKING_FRAMES[0],
                                  classes="bubble ai-bubble thinking")
        turn.mount(thinking_bubble)

        convo.scroll_end(animate=False)

        self._cur_tree = tree
        self._cur_ai_bubble = ai_bubble
        self._cur_ai_text = ""
        self._cur_thinking = thinking_bubble
        self._cur_tool_node = None

    def _do_add_tool_node(self, tool_id, name, detail):
        if self._cur_tree is None:
            return
        data = ToolNodeData(name, detail)
        node = self._cur_tree.root.add(tool_label(data), data=data)
        self._node_registry[tool_id] = node
        self._all_tool_nodes.append((node, self._cur_tree))
        self._cur_tool_node = node
        self._cur_tree.refresh()

    def _do_finish_tool_node(self, tool_id, result_text, stat):
        node = self._node_registry.pop(tool_id, None)
        if node is None:
            return
        data = node.data
        data.done = True
        data.result = result_text
        data.stat = stat
        node.set_label(tool_label(data))
        node.remove_children()
        for ln in (result_text.splitlines() or [""]):
            node.add_leaf(Text(ln, style="dim"))
        if self._cur_tool_node is node:
            self._cur_tool_node = None
        for n, t in self._all_tool_nodes:
            if n is node:
                t.refresh()
                break

    def _do_append_text(self, text):
        if self._cur_ai_bubble is None:
            return
        self._cur_ai_text += text
        self._cur_ai_bubble.update(self._cur_ai_text)
        convo = self.query_one("#convo", VerticalScroll)
        convo.scroll_end(animate=False)

    def _do_hide_thinking(self):
        if self._cur_thinking is not None:
            self._cur_thinking.remove()
            self._cur_thinking = None

    def _do_end_turn(self):
        self.busy = False
        self._set_status(f"  {CHECK}  {MODEL_NAME}  \u00b7  ready", "#4ade80")
        self._do_hide_thinking()
        self._cur_tree = None
        self._cur_ai_bubble = None
        self._cur_tool_node = None

    def _do_worker_closed(self):
        self.busy = False
        self._set_status("  worker closed the connection", "#f87171")
        self._do_hide_thinking()
        if self._cur_ai_bubble is not None:
            self._cur_ai_bubble.update("(worker closed the connection)")

    # ── Key bindings ─────────────────────────────────────────────────────────

    def action_toggle_last_tool(self):
        if self._all_tool_nodes:
            node, _ = self._all_tool_nodes[-1]
            node.toggle()

    def action_expand_all(self):
        for node, tree in self._all_tool_nodes:
            node.expand()

    def action_collapse_all(self):
        for node, tree in self._all_tool_nodes:
            node.collapse()

    # ── Input ────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.lower() == "q":
            self.exit()
            return
        self.run_turn(text)

    # ── Turn worker (background thread, NEVER blocks) ───────────────────────

    @work(exclusive=True, thread=True)
    def run_turn(self, text):
        self.busy = True

        # Mount turn container — fire and forget
        self.ui_q.put(MountTurn(text))

        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

        tool_id_counter = 0
        pending_tool_id = None
        thinking_hidden = False

        while True:
            item = self.raw_q.get()
            if item is SENTINEL:
                self.ui_q.put(WorkerClosed())
                return

            line = item.strip()

            if line.startswith("<<TOOL_START>>"):
                try:
                    payload = json.loads(line[len("<<TOOL_START>>"):])
                    name, detail = payload["name"], payload["detail"]
                except (json.JSONDecodeError, KeyError):
                    name, detail = "tool", "(unparsed)"
                tool_id_counter += 1
                pending_tool_id = tool_id_counter
                self.ui_q.put(AddToolNode(tool_id_counter, name, detail))
                continue

            if line.startswith("<<TOOL_END>>"):
                try:
                    payload = json.loads(line[len("<<TOOL_END>>"):])
                    result = payload["result"]
                    result_text = json.dumps(result, indent=2)
                except (json.JSONDecodeError, KeyError):
                    result = None
                    result_text = "(unparsed result)"
                if pending_tool_id is not None:
                    stat = diff_stat(result)
                    self.ui_q.put(FinishToolNode(pending_tool_id,
                                                  result_text, stat))
                pending_tool_id = None
                continue

            if line == "<<END>>":
                self.ui_q.put(EndTurn())
                return

            if line:
                # Hide thinking on FIRST text line
                if not thinking_hidden:
                    thinking_hidden = True
                    self.ui_q.put(HideThinking())
                self.ui_q.put(AppendText(line + " "))


def main():
    talk = "--talk" in sys.argv[1:]
    app = PriyaApp(talk=talk)
    app.run()


if __name__ == "__main__":
    main()
