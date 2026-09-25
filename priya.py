"""
Priya TUI — pure terminal, raw ANSI. No Textual. No curses. Just bash vibes.

Feel: vim / lazygit / htop. You're IN the terminal, not wrapped by one.

Layout:
  ┌─────────────────────────────────┐
  │  conversation history (scrolls) │
  │                                 │
  ├─────────────────────────────────┤
  │  status bar                     │
  ├─────────────────────────────────┤
  │  > input                        │
  └─────────────────────────────────┘

Keys:
  Enter        send
  Esc          interrupt
  Ctrl+C       quit
  Ctrl+U       clear input
  Ctrl+L       redraw
  Up/Down      scroll history
  PgUp/PgDn    scroll history fast
"""

import os
import sys
import json
import queue
import signal
import struct
import fcntl
import termios
import tty
import threading
import subprocess
import textwrap
import time
from collections import deque

# ── Constants ─────────────────────────────────────────────────────────────────

DIR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(DIR, "live_cli.py")
MODEL_NAME = "mistral-medium-latest"
SENTINEL = object()

SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
THINKING = [f"{f} thinking…" for f in SPINNER]

# ── ANSI helpers ──────────────────────────────────────────────────────────────

ESC = "\033"
CSI = ESC + "["

def ansi(*codes): return CSI + ";".join(str(c) for c in codes) + "m"
def cup(row, col): return f"{CSI}{row};{col}H"   # move cursor
def el(n=0):       return f"{CSI}{n}K"            # erase line (0=to end)
def ed(n=0):       return f"{CSI}{n}J"            # erase display
def sc():          return ESC + "7"               # save cursor
def rc():          return ESC + "8"               # restore cursor
def hide_cursor(): return CSI + "?25l"
def show_cursor(): return CSI + "?25h"
def alt_screen():  return CSI + "?1049h"
def main_screen(): return CSI + "?1049l"
def smcup():       return alt_screen()
def rmcup():       return main_screen()

RESET    = ansi(0)
BOLD     = ansi(1)
DIM      = ansi(2)
ITALIC   = ansi(3)

def fg(r,g,b): return f"{CSI}38;2;{r};{g};{b}m"
def bg(r,g,b): return f"{CSI}48;2;{r};{g};{b}m"

# Palette
C_USER    = fg(230,230,230)   # near white
C_AI      = fg(125,185,232)   # sky blue
C_DIM     = fg(80,80,90)
C_TOOL    = fg(74,222,128)    # green
C_TOOL_B  = fg(96,165,250)    # blue (artifact)
C_TOOL_P  = fg(196,181,253)   # purple (edit)
C_TOOL_Y  = fg(250,204,21)    # yellow (ask)
C_STDOUT  = fg(120,130,150)
C_STDERR  = fg(161,138,102)
C_ERR     = fg(251,113,133)   # red
C_WARN    = fg(250,204,21)    # yellow
C_OK      = fg(74,222,128)    # green
C_STATUS  = fg(100,100,110)
C_READY   = fg(74,222,128)
C_BORDER  = fg(60,60,70)
C_PROMPT  = fg(96,165,250)
C_HEAD    = fg(100,149,237)   # cornflower logo
C_CYAN    = fg(103,232,249)
C_DIFF_A  = fg(74,222,128)
C_DIFF_D  = fg(251,113,133)
C_DIFF_N  = fg(209,213,219)
BG_MAIN   = bg(18,18,18)
BG_INPUT  = bg(30,30,36)
BG_STATUS = bg(15,15,18)

TOOL_COLORS = {
    "bash":            C_TOOL,
    "artifact":        C_TOOL_B,
    "edit":            C_TOOL_P,
    "read":            C_CYAN,
    "askuserquestion": C_TOOL_Y,
}

LOGO = [
    " ▄▓▀▄   ▄▓▀▄   ▄▓  ▄▓ ▄   ▄▓▀▄ ",
    "█▓▒ ▒▓ █▓▒ ▒▓ █▓▒ █▓▒ ▒▓ █▓▒ ▒▓",
    "▓▒░▄▀  ▓▒░▄▀  ▀▄▀ ▀▒░▄░▒ ▓▒░▄░▒",
    "▒░     ▒░  █▄ ▒░  ▄▄▄  ░ ▒░   ░",
    "░ ░    ░ ░ ░  ░ ░ ░ ░ ░  ░ ░ ░ ",
    " ░▒     ░▒ ▒░  ░▒  ░▒ ▒░  ░▒ ▒░",
    "▀▒▓    ▀▒▓ ▓█ ▀▒▓ ▀▒▓ ▓▀ ▀▒▓ ▓▀",
    "  ▀      ▀ ▒    ▀   ▀▀▒    ▀ ▒ ",
]

VERSION = "v0.156.1"

def build_splash(w, model_name, cwd):
    """Render the splash box. Returns list of ANSI lines (each fills exactly w chars)."""
    inner = w - 2
    logo_w = max(len(l) for l in LOGO)

    def box_line(content_ansi, content_plain):
        pad = max(0, inner - len(content_plain))
        return C_BORDER + "│" + RESET + content_ansi + " " * pad + C_BORDER + "│" + RESET

    out = []

    # top border
    out.append(C_BORDER + "╭" + "─" * inner + "╮" + RESET)

    # title
    title_plain = f" >_ Priya  ({VERSION})"
    out.append(box_line(BOLD + C_HEAD + title_plain + RESET, title_plain))

    # blank
    out.append(box_line("", ""))

    # logo (centred)
    pad_left = max(0, (inner - logo_w) // 2)
    for row in LOGO:
        plain = " " * pad_left + row
        out.append(box_line(C_HEAD + plain + RESET, plain))

    # blank
    out.append(box_line("", ""))

    # metadata
    meta = [
        ("model",       model_name, "/model to change"),
        ("directory",   cwd,        ""),
        ("permissions", "YOLO mode", ""),
    ]
    key_w = max(len(k) for k, _, _ in meta)
    for key, val, hint in meta:
        hint_ansi  = ("  " + C_DIM + hint + RESET) if hint else ""
        hint_plain = ("  " + hint) if hint else ""
        label = f"{key}:".ljust(key_w + 1)
        ansi  = f"  {C_DIM}{label}{RESET}  {C_AI}{val}{RESET}{hint_ansi}"
        plain = f"  {label}  {val}{hint_plain}"
        out.append(box_line(ansi, plain))

    # bottom border
    out.append(C_BORDER + "╰" + "─" * inner + "╯" + RESET)

    return out

# ── Terminal size ─────────────────────────────────────────────────────────────

def terminal_size():
    try:
        h, w = struct.unpack("hh", fcntl.ioctl(1, termios.TIOCGWINSZ, b"\0"*4))
        return max(w, 40), max(h, 10)
    except Exception:
        return 80, 24

# ── Line rendering helpers ────────────────────────────────────────────────────

def truncate(s, width):
    """Truncate a plain string to fit terminal width."""
    if len(s) <= width:
        return s
    return s[:width-1] + "…"

def wrap_text(text, width, indent=0):
    """Wrap plain text to lines of given width."""
    prefix = " " * indent
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        for line in textwrap.wrap(para, width - indent) or [""]:
            lines.append(prefix + line)
    return lines

def colorize_log(text, stream="stdout"):
    """Apply ANSI colors to a tool log line."""
    base = C_STDOUT if stream == "stdout" else C_STDERR
    # Simple keyword coloring
    import re
    result = base + text + RESET
    return result

def colorize_diff(diff):
    """Return list of colored diff lines."""
    out = []
    for line in diff.split("\n"):
        if line.startswith("+++") or (line.startswith("+") and not line.startswith("+++")):
            out.append(C_DIFF_A + line + RESET)
        elif line.startswith("---") or (line.startswith("-") and not line.startswith("---")):
            out.append(C_DIFF_D + line + RESET)
        else:
            out.append(C_DIFF_N + line + RESET)
    return out

def render_markdown_ansi(text, width, indent=2):
    import mistune
    import textwrap as _tw
    import re as _re

    C_H1       = fg(100, 149, 237)
    C_H2       = fg(96,  165, 250)
    C_H3       = fg(103, 232, 249)
    C_CODE     = fg(250, 204,  21)
    C_FENCE    = fg(120, 130, 150)
    C_FENCE_BG = bg(28,  28,  36)
    C_QUOTE    = fg(160, 160, 170)
    C_LI       = fg(74,  222, 128)
    C_RULE_C   = fg(60,   60,  70)

    pad     = " " * indent
    inner_w = max(20, width - indent)
    out     = []

    def render_inline(children):
        parts = []
        for node in (children or []):
            t = node.get("type")
            if t == "text":
                parts.append(C_AI + node.get("raw", ""))
            elif t == "softbreak":
                parts.append(" ")
            elif t == "linebreak":
                parts.append("\n")
            elif t == "codespan":
                parts.append(C_CODE + node.get("raw", "") + RESET)
            elif t == "strong":
                parts.append(BOLD + C_AI + render_inline(node.get("children", [])) + RESET)
            elif t == "emphasis":
                parts.append(ansi(3) + C_AI + render_inline(node.get("children", [])) + RESET)
            elif t == "link":
                parts.append(C_AI + render_inline(node.get("children", [])))
            else:
                if "children" in node:
                    parts.append(render_inline(node["children"]))
                elif "raw" in node:
                    parts.append(C_AI + node["raw"])
        return "".join(parts)

    def plain(children):
        return _re.sub(r"\033\[[^m]*m", "", render_inline(children))

    def render_block(node, list_depth=0, ordered=False, counter=None):
        t = node.get("type")
        if t == "blank_line":
            out.append("")
        elif t == "thematic_break":
            out.append(pad + C_RULE_C + "─" * inner_w + RESET)
        elif t == "heading":
            raw_txt = plain(node.get("children", []))
            out.append(pad + C_AI + BOLD + raw_txt + RESET)
        elif t == "block_code":
            lang = (node.get("attrs") or {}).get("info") or ""
            out.append(pad + C_RULE_C + "─" * inner_w + RESET)
            if lang:
                out.append(pad + C_FENCE + BOLD + " " + lang + " " + RESET)
            for code_line in node.get("raw", "").rstrip("\n").split("\n"):
                while len(code_line) > inner_w - 2:
                    out.append(pad + C_FENCE_BG + C_FENCE + code_line[:inner_w-2] + RESET)
                    code_line = "  " + code_line[inner_w-2:]
                out.append(pad + C_FENCE_BG + C_FENCE + code_line + RESET)
            out.append(pad + C_RULE_C + "─" * inner_w + RESET)
        elif t == "block_quote":
            for child in node.get("children", []):
                raw_txt = plain(child.get("children", []))
                for wl in _tw.wrap(raw_txt, inner_w - 4) or [""]:
                    out.append(pad + C_RULE_C + "▌ " + RESET + C_QUOTE + wl + RESET)
        elif t == "list":
            is_ordered = node.get("attrs", {}).get("ordered", False)
            depth      = node.get("attrs", {}).get("depth", 0)
            cnt        = [1]
            for item in node.get("children", []):
                render_block(item, list_depth=depth, ordered=is_ordered, counter=cnt)
        elif t == "list_item":
            all_children = []
            for child in node.get("children", []):
                all_children.extend(child.get("children", []))
            raw_txt   = plain(all_children)
            extra_pad = "  " * list_depth
            avail     = max(10, inner_w - 4 - (list_depth * 2))
            words     = _tw.wrap(raw_txt, avail) or [""]
            if ordered and counter is not None:
                bullet = C_LI + f"{counter[0]}. " + RESET
                blen   = len(f"{counter[0]}. ")
                counter[0] += 1
            else:
                bullet = C_LI + "• " + RESET
                blen   = 2
            for j, wl in enumerate(words):
                if j == 0:
                    out.append(pad + extra_pad + bullet + C_AI + wl + RESET)
                else:
                    out.append(pad + extra_pad + " " * blen + C_AI + wl + RESET)
        elif t in ("paragraph", "block_text"):
            children = node.get("children", [])
            raw_txt  = plain(children)
            for wl in _tw.wrap(raw_txt, inner_w) or [""]:
                out.append(pad + C_AI + wl + RESET)
            out.append("")
        else:
            for child in node.get("children", []):
                render_block(child)

    md_parse = mistune.create_markdown(renderer=None)
    try:
        ast = md_parse(text)
    except Exception:
        for line in text.split("\n"):
            for wl in _tw.wrap(line, inner_w) or [""]:
                out.append(pad + C_AI + wl + RESET)
        return out

    for node in (ast or []):
        render_block(node)

    return out
# ── Conversation model ────────────────────────────────────────────────────────
# Each turn is a list of "blocks". A block is a dict with a "type" key.
# Types: user_msg, ai_text, tool_call, tool_log, tool_done, question, edit_approval, divider

class Turn:
    def __init__(self, user_text):
        self.user_text = user_text
        self.blocks = []          # rendered line-groups
        self.ai_lines = []        # raw AI text lines accumulated
        self.tool_nodes = {}      # tool_id → ToolNode
        self.tool_order = []      # ordered tool_ids

class ToolNode:
    def __init__(self, tool_id, name, detail):
        self.tool_id = tool_id
        self.name = name
        self.detail = detail
        self.logs = []            # (stream, text)
        self.done = False
        self.result_text = None
        self.expanded = False     # user can toggle

# ── Screen renderer ───────────────────────────────────────────────────────────

class Screen:
    """
    Owns the terminal. Renders everything from scratch on each redraw.
    All public methods are called from the main thread only.
    """

    def __init__(self):
        self.turns = []           # list of Turn
        self.cur_turn = None      # Turn being built
        self.status_text = ""
        self.status_color = C_STATUS
        self.input_text = ""
        self.input_cursor = 0
        self.scroll_offset = 0    # lines scrolled up from bottom
        self.spinner_i = 0
        self.busy = False
        self.thinking = False
        self._lines_cache = []    # flat list of rendered lines for the convo
        self._cache_dirty = True
        self._w = 80
        self._h = 24
        self._convo_h = 20        # lines available for conversation
        self._question_state = None
        self._edit_state = None

        # Enter alt screen, hide cursor
        self._write(smcup() + hide_cursor() + BG_MAIN + ed(2))
        self._update_size()
        self._draw_logo()

    def _write(self, s):
        sys.stdout.write(s)
        sys.stdout.flush()

    def _update_size(self):
        w, h = terminal_size()
        self._w = w
        self._h = h
        # Logo is scrollable content now, not a fixed block at top
        # Reserve only: status(1) + border-top(1) + input(1) + border-bot(1) = 4
        self._convo_h = max(4, h - 4)
        self._cache_dirty = True

    # ── Logo ──────────────────────────────────────────────────────

    def _draw_logo(self):
        # Logo is now part of scrollable content — just clear screen on init
        self._write(BG_MAIN + ed(2))
        self._logo_rows = 0

    # ── Full redraw ───────────────────────────────────────────────

    def redraw(self):
        self._update_size()
        buf = []
        buf.append(BG_MAIN)

        # Convo area fills full screen (logo scrolls inside it)
        convo_start = 1
        lines = self._get_lines()
        visible_start = max(0, len(lines) - self._convo_h - self.scroll_offset)
        visible = lines[visible_start: visible_start + self._convo_h]

        for i in range(self._convo_h):
            row = convo_start + i
            buf.append(cup(row, 1) + el())
            if i < len(visible):
                buf.append(visible[i])
        
        # Thinking spinner (last convo row if busy)
        if self.busy and self.thinking:
            row = convo_start + min(len(visible), self._convo_h - 1)
            frame = THINKING[self.spinner_i % len(THINKING)]
            buf.append(cup(row, 1) + el() + C_DIM + "  " + frame + RESET)

        # Status bar
        status_row = convo_start + self._convo_h
        buf.append(cup(status_row, 1) + el() + BG_STATUS + self.status_color)
        buf.append(truncate(self.status_text, self._w))
        buf.append(RESET)

        # Input bar — top border / prompt line / bottom border
        border_top_row = status_row + 1
        input_row      = border_top_row + 1
        border_bot_row = input_row + 1

        border = C_BORDER + ("─" * self._w) + RESET
        buf.append(cup(border_top_row, 1) + el() + BG_MAIN + border)

        prompt = C_PROMPT + BOLD + "> " + RESET + BG_MAIN
        display_text = self.input_text
        max_input = self._w - 3
        if len(display_text) > max_input:
            display_text = display_text[len(display_text) - max_input:]
        buf.append(cup(input_row, 1) + el() + BG_MAIN)
        buf.append(prompt + C_USER + display_text + RESET)

        buf.append(cup(border_bot_row, 1) + el() + BG_MAIN + border)

        cursor_col = 3 + min(self.input_cursor, max_input)
        buf.append(cup(input_row, cursor_col))
        buf.append(show_cursor())

        self._write("".join(buf))

    # ── Line cache ────────────────────────────────────────────────

    def _get_lines(self):
        if not self._cache_dirty:
            return self._lines_cache
        lines = []
        w = self._w - 2  # side margin

        # Logo as scrollable header — tied to chat, scrolls up with it
        splash = build_splash(self._w, MODEL_NAME, os.getcwd())
        for line in splash:
            lines.append(BG_MAIN + line + RESET)
        lines.append("")  # breathing room before first turn

        for turn in self.turns:
            # User message
            user_lines = wrap_text(turn.user_text, w - 4, indent=0)
            for l in user_lines:
                lines.append("  " + C_USER + l + RESET)
            lines.append("")

            # Tool nodes first
            for tid in turn.tool_order:
                node = turn.tool_nodes[tid]
                tc = TOOL_COLORS.get(node.name.lower(), C_AI)
                spinner_f = SPINNER[self.spinner_i % len(SPINNER)]
                icon_c = C_OK if node.done else C_DIM
                detail_s = f"({node.detail})" if node.detail else ""
                # Header: ToolName(detail):
                lines.append(
                    f"  {BOLD}{tc}{node.name}{RESET}"
                    f"{C_DIM}{detail_s}:{RESET}"
                )
                # Logs indented beneath, right-aligned look
                for stream, log_text in node.logs[-40:]:
                    lc = C_STDOUT if stream == "stdout" else C_STDERR
                    for ll in wrap_text(log_text, w - 10, indent=0):
                        lines.append(f"          {lc}{ll}{RESET}")
                if not node.done:
                    lines.append(f"          {C_DIM}{spinner_f}{RESET}")
                lines.append("")

            # AI text after tool calls
            if turn.ai_lines:
                if turn is not self.cur_turn:
                    # Turn complete — render markdown
                    full_ai_text = "\n".join(turn.ai_lines)
                    for ll in render_markdown_ansi(full_ai_text, w - 2, indent=2):
                        lines.append(ll)
                else:
                    # Still streaming — plain wrap to avoid glitches
                    for al in turn.ai_lines:
                        for ll in wrap_text(al, w - 4, indent=0):
                            lines.append("  " + C_AI + ll + RESET)
                lines.append("")

            # Question
            if turn is self.cur_turn and self._question_state:
                qs = self._question_state
                q = qs["questions"][qs["index"]]
                lines.append("  " + C_TOOL_Y + BOLD + "? " + q.get("header","") + RESET)
                lines.append("  " + C_AI + q["question"] + RESET)
                for opt in q["options"]:
                    lines.append(f"    {C_DIM}[{opt['label']}]{RESET} {C_AI}{opt['description']}{RESET}")
                lines.append("  " + C_DIM + "Type your answer below ↓" + RESET)
                lines.append("")

            # Edit approval
            if turn is self.cur_turn and self._edit_state:
                es = self._edit_state
                lines.append("  " + C_TOOL_P + BOLD + "✎ Edit approval: " + RESET + C_AI + es["path"] + RESET)
                diff_lines = colorize_diff(es["diff"])
                for dl in diff_lines[:30]:  # cap diff preview
                    lines.append("    " + dl)
                lines.append("  " + C_DIM + "Type 'y' to approve or 'n' to cancel ↓" + RESET)
                lines.append("")

        self._lines_cache = lines
        self._cache_dirty = False
        return lines

    def _dirty(self):
        self._cache_dirty = True

    # ── Public state mutators (main thread only) ──────────────────

    def set_status(self, text, color=None):
        self.status_text = text
        self.status_color = color or C_STATUS

    def set_busy(self, busy):
        self.busy = busy
        self.thinking = busy
        self._dirty()

    def tick_spinner(self):
        self.spinner_i += 1
        # Only dirty the cache if something is visibly spinning
        has_active_tool = any(
            not node.done
            for turn in self.turns
            for node in turn.tool_nodes.values()
        )
        if has_active_tool or self.thinking:
            self._cache_dirty = True

    def new_turn(self, user_text):
        t = Turn(user_text)
        self.turns.append(t)
        self.cur_turn = t
        self.scroll_offset = 0
        self._dirty()
        return t

    def add_tool_node(self, tool_id, name, detail):
        if self.cur_turn is None:
            return
        node = ToolNode(tool_id, name, detail)
        self.cur_turn.tool_nodes[tool_id] = node
        self.cur_turn.tool_order.append(tool_id)
        self._dirty()

    def append_tool_log(self, tool_id, stream, text):
        if self.cur_turn is None:
            return
        node = self.cur_turn.tool_nodes.get(tool_id)
        if node:
            node.logs.append((stream, text))
            self._dirty()

    def finish_tool_node(self, tool_id, result_text):
        if self.cur_turn is None:
            return
        node = self.cur_turn.tool_nodes.get(tool_id)
        if node:
            node.done = True
            node.result_text = result_text
            self._dirty()

    def toggle_last_tool(self):
        if self.cur_turn and self.cur_turn.tool_order:
            tid = self.cur_turn.tool_order[-1]
            node = self.cur_turn.tool_nodes[tid]
            node.expanded = not node.expanded
            self._dirty()

    def append_ai_text(self, text):
        if self.cur_turn is None:
            return
        self.thinking = False
        self.cur_turn.ai_lines.append(text)
        self._dirty()

    def end_turn(self):
        self.cur_turn = None
        self._question_state = None
        self._edit_state = None
        self._dirty()

    def set_question(self, state):
        self._question_state = state
        self._dirty()

    def set_edit(self, state):
        self._edit_state = state
        self._dirty()

    def scroll_up(self, n=3):
        lines = self._get_lines()
        max_scroll = max(0, len(lines) - self._convo_h)
        self.scroll_offset = min(self.scroll_offset + n, max_scroll)

    def scroll_down(self, n=3):
        self.scroll_offset = max(0, self.scroll_offset - n)

    def input_insert(self, ch):
        self.input_text = (self.input_text[:self.input_cursor]
                           + ch
                           + self.input_text[self.input_cursor:])
        self.input_cursor += 1

    def input_backspace(self):
        if self.input_cursor > 0:
            self.input_text = (self.input_text[:self.input_cursor-1]
                               + self.input_text[self.input_cursor:])
            self.input_cursor -= 1

    def input_clear(self):
        self.input_text = ""
        self.input_cursor = 0

    def input_left(self):
        self.input_cursor = max(0, self.input_cursor - 1)

    def input_right(self):
        self.input_cursor = min(len(self.input_text), self.input_cursor + 1)

    def input_home(self):
        self.input_cursor = 0

    def input_end(self):
        self.input_cursor = len(self.input_text)

    def take_input(self):
        text = self.input_text
        self.input_clear()
        return text

    def cleanup(self):
        self._write(show_cursor() + rmcup() + RESET)

# ── Input reading ─────────────────────────────────────────────────────────────

def read_key(fd):
    """Read one keypress from raw terminal fd. Returns a string token."""
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        # 20ms gap so the full escape sequence arrives before non-blocking read
        try:
            time.sleep(0.02)
            fcntl.fcntl(fd, fcntl.F_SETFL, os.O_NONBLOCK)
            rest = b""
            try:
                rest = os.read(fd, 8)
            except BlockingIOError:
                pass
            finally:
                fcntl.fcntl(fd, fcntl.F_SETFL, 0)
        except Exception:
            rest = b""
        seq = ch + rest
        mapping = {
            b"\x1b[A": "UP",
            b"\x1b[B": "DOWN",
            b"\x1b[C": "RIGHT",
            b"\x1b[D": "LEFT",
            b"\x1b[5~": "PGUP",
            b"\x1b[6~": "PGDN",
            b"\x1b[H": "HOME",
            b"\x1b[F": "END",
            b"\x1b": "ESC",
        }
        return mapping.get(seq, mapping.get(ch, f"ESC_SEQ:{seq.hex()}"))
    # Control chars
    ctrl = {
        b"\r": "ENTER",
        b"\n": "ENTER",
        b"\x7f": "BACKSPACE",
        b"\x08": "BACKSPACE",
        b"\x03": "CTRL_C",
        b"\x15": "CTRL_U",
        b"\x0c": "CTRL_L",
        b"\x01": "HOME",
        b"\x05": "END",
    }
    return ctrl.get(ch, ch.decode("utf-8", errors="replace"))

# ── Worker I/O ────────────────────────────────────────────────────────────────

def reader_thread(proc, q):
    for line in iter(proc.stdout.readline, ""):
        q.put(line)
    q.put(SENTINEL)

# ── Main app ──────────────────────────────────────────────────────────────────

class PriyaApp:
    def __init__(self):
        self.screen = Screen()
        self.raw_q = queue.Queue()
        self.proc = None
        self._stdin_lock = threading.Lock()
        self._busy = False
        self._tool_active = False   # True while tool calls are executing
        self._question_state = None
        self._edit_state = None
        self._interrupted = False
        self._running = True

    def start(self):
        self._start_worker()
        # Spinner thread
        threading.Thread(target=self._spinner_loop, daemon=True).start()
        # Protocol reader thread
        threading.Thread(target=self._protocol_loop, daemon=True).start()
        self.screen.set_status("", C_STATUS)
        self.screen.redraw()
        self._input_loop()

    def _start_worker(self):
        self.proc = subprocess.Popen(
            [sys.executable, WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open("/tmp/priya_worker.log", "w"),
            text=True, bufsize=1,
        )
        threading.Thread(target=reader_thread,
                         args=(self.proc, self.raw_q), daemon=True).start()

    # ── Spinner ───────────────────────────────────────────────────

    def _spinner_loop(self):
        while self._running:
            self.screen.tick_spinner()
            self.screen.redraw()
            time.sleep(0.1)

    # ── Protocol parser ───────────────────────────────────────────

    def _protocol_loop(self):
        deferred_finishes = []
        thinking_hidden = False

        while self._running:
            item = self.raw_q.get()
            if item is SENTINEL:
                self.screen.set_status("  worker closed — restarting…", C_WARN)
                time.sleep(0.5)
                self._start_worker()
                continue

            line = item.strip()
            if not line:
                continue

            if line.startswith("<<TOOL_START>>"):
                try:
                    p = json.loads(line[len("<<TOOL_START>>"):])
                    tid = p.get("id", f"t{id(p)}")
                    self.screen.add_tool_node(tid, p["name"], p.get("detail",""))
                    self._tool_active = True
                except Exception:
                    pass
                continue

            if line.startswith("<<TOOL_LOG>>"):
                try:
                    p = json.loads(line[len("<<TOOL_LOG>>"):])
                    self.screen.append_tool_log(p["id"], p.get("stream","stdout"), p["text"])
                except Exception:
                    pass
                continue

            if line.startswith("<<TOOL_END>>"):
                try:
                    p = json.loads(line[len("<<TOOL_END>>"):])
                    tid = p.get("id")
                    result_text = json.dumps(p.get("result",""), indent=2)
                    deferred_finishes.append((tid, result_text))
                except Exception:
                    pass
                continue

            if line.startswith("<<ASK_USER_QUESTION>>"):
                try:
                    p = json.loads(line[len("<<ASK_USER_QUESTION>>"):])
                    self._question_state = {
                        "id": p["id"],
                        "questions": p["questions"],
                        "answers": [],
                        "index": 0,
                    }
                    self.screen.set_question(self._question_state)
                    self.screen.set_status("  ?  Waiting for your answer", C_TOOL_Y)
                except Exception:
                    pass
                continue

            if line.startswith("<<EDIT_APPROVAL>>"):
                try:
                    p = json.loads(line[len("<<EDIT_APPROVAL>>"):])
                    self._edit_state = {
                        "id": p["id"],
                        "path": p["path"],
                        "diff": p["diff"],
                    }
                    self.screen.set_edit(self._edit_state)
                    self.screen.set_status("  ✎  Review edit — type y/n", C_TOOL_P)
                except Exception:
                    pass
                continue

            if line.startswith("<<SCHEDULED_TASK>>"):
                try:
                    p = json.loads(line[len("<<SCHEDULED_TASK>>"):])
                    text = p.get("prompt","").strip()
                    if text:
                        self.screen.new_turn(f"⏰ Scheduled: {text}")
                        thinking_hidden = False
                        deferred_finishes.clear()
                except Exception:
                    pass
                continue

            if line.startswith("<<AGENTJOB_RESULT>>"):
                continue  # handled implicitly via tool logs

            if line == "<<END>>":
                for tid, rt in deferred_finishes:
                    self.screen.finish_tool_node(tid, rt)
                deferred_finishes.clear()
                self._busy = False
                self._tool_active = False
                self._interrupted = False
                self.screen.set_busy(False)
                self.screen.end_turn()
                self.screen.set_status("", C_STATUS)
                thinking_hidden = False
                continue

            # Plain text → AI response
            if not thinking_hidden:
                thinking_hidden = True
                self.screen.thinking = False
            for tid, rt in deferred_finishes:
                self.screen.finish_tool_node(tid, rt)
            deferred_finishes.clear()
            self.screen.append_ai_text(line)

    # ── Input loop ────────────────────────────────────────────────

    def _input_loop(self):
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while self._running:
                key = read_key(fd)
                self._handle_key(key)
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            self.screen.cleanup()
            if self.proc:
                self.proc.terminate()

    def _handle_key(self, key):
        s = self.screen

        if key == "CTRL_C":
            self._running = False
            return

        if key == "CTRL_L":
            s.redraw()
            return

        if key == "UP":
            s.scroll_up(3)
            return

        if key == "DOWN":
            s.scroll_down(3)
            return

        if key == "PGUP":
            s.scroll_up(s._convo_h - 2)
            return

        if key == "PGDN":
            s.scroll_down(s._convo_h - 2)
            return

        if key == "ESC":
            self._do_interrupt()
            return

        if key == "CTRL_U":
            s.input_clear()
            return

        if key == "LEFT":
            s.input_left()
            return

        if key == "RIGHT":
            s.input_right()
            return

        if key == "HOME":
            s.input_home()
            return

        if key == "END":
            s.input_end()
            return

        if key == "BACKSPACE":
            s.input_backspace()
            return

        if key == "ENTER":
            self._do_submit()
            return

        # Printable character
        if len(key) == 1 and key.isprintable():
            s.input_insert(key)
            return

    def _do_submit(self):
        text = self.screen.take_input().strip()
        if not text:
            return

        # Question answer
        if self._question_state is not None:
            qs = self._question_state
            qs["answers"].append(text)
            qs["index"] += 1
            if qs["index"] < len(qs["questions"]):
                self.screen.set_question(qs)
            else:
                payload = {"id": qs["id"], "answers": qs["answers"]}
                self._question_state = None
                self.screen.set_question(None)
                self._send_line("<<ASK_USER_ANSWER>>" + json.dumps(payload))
            return

        # Edit approval
        if self._edit_state is not None:
            approved = text.casefold() in ("y", "yes", "approve", "approved")
            cancelled = text.casefold() in ("n", "no", "cancel")
            if approved or cancelled:
                payload = {"id": self._edit_state["id"], "approved": approved}
                self._edit_state = None
                self.screen.set_edit(None)
                self._send_line("<<EDIT_APPROVAL>>" + json.dumps(payload))
            return

        if text.lower() == "q":
            self._running = False
            return

        # Normal turn
        self._busy = True
        self._interrupted = False
        self.screen.new_turn(text)
        self.screen.set_busy(True)
        self.screen.set_status("", C_STATUS)
        threading.Thread(target=self._send_line, args=(text,), daemon=True).start()

    def _do_interrupt(self):
        if not self._busy:
            return
        self._interrupted = True
        self._busy = False
        self.screen.set_busy(False)
        self.screen.set_status("  ⊘  Interrupted", C_DIM)
        self._send_line("<<PRIYA_INTERRUPT>>")

    def _send_line(self, text):
        try:
            with self._stdin_lock:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.screen.set_status("  ✗  worker pipe broken", C_ERR)

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Make stdout unbuffered
    sys.stdout = open(sys.stdout.fileno(), "w", buffering=1, closefd=False)
    app = PriyaApp()
    try:
        app.start()
    except KeyboardInterrupt:
        pass
    finally:
        app.screen.cleanup()
        sys.exit(0)

if __name__ == "__main__":
    main()
