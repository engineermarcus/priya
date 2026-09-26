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
  Enter        send / select option / approve edit
  Esc          interrupt / reject edit / clear input
  Tab          autocomplete slash command / toggle tool log folding
  Ctrl+O       toggle tool log folding (compact vs full)
  Ctrl+C       quit (or clear input if typing)
  Ctrl+U       clear input
  Ctrl+L       redraw
  Up/Down      browse command history (in prompt) or scroll history
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
import re
import select
from datetime import datetime, timedelta

import mistune
import pygments
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import TerminalTrueColorFormatter

# ── Constants ─────────────────────────────────────────────────────────────────

DIR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(DIR, "live_cli.py")
MODEL_NAME = "mistral-medium-latest"
VERSION = "v0.156.1"
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
def enable_mouse():  return CSI + "?1000h" + CSI + "?1006h"
def disable_mouse(): return CSI + "?1006l" + CSI + "?1000l"
def smcup():       return alt_screen()
def rmcup():       return main_screen()

RESET    = ansi(0)
BOLD     = ansi(1)
DIM      = ansi(2)
ITALIC   = ansi(3)

def fg(r,g,b): return f"{CSI}38;2;{r};{g};{b}m"
def bg(r,g,b): return f"{CSI}48;2;{r};{g};{b}m"

# Palette (Exact Original Priya Colors)
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
    "bash":                 C_TOOL,
    "artifact":             C_TOOL_B,
    "edit":                 C_TOOL_P,
    "read":                 C_CYAN,
    "grep":                 C_WARN,
    "glob":                 C_WARN,
    "lsp":                  C_TOOL_P,
    "agentjob":             C_HEAD,
    "croncreate":           C_WARN,
    "cronlist":             C_WARN,
    "crondelete":           C_WARN,
    "listmcpresourcestool": C_TOOL,
    "enterplanmode":        C_WARN,
    "exitplanmode":         C_WARN,
    "askuserquestion":      C_TOOL_Y,
}

COMMANDS = [
    ("/help", "Show commands, keybindings & guide"),
    ("/status", "Display workspace, git & session status"),
    ("/diff", "Show git diff of unstaged changes"),
    ("/clear", "Clear conversation screen"),
    ("/tools", "List all available Priya tools"),
    ("/model", "View active model information"),
    ("/history", "Browse command history"),
    ("/compact", "Toggle compact / expanded tool logs"),
    ("/exit", "Exit Priya"),
]

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

# ── ANSI Helpers ──────────────────────────────────────────────────────────────

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

def strip_ansi(s):
    return ANSI_RE.sub("", s)

def vis_len(s):
    return len(strip_ansi(s))

def truncate(s, width):
    """Truncate a plain string to fit terminal width."""
    if len(s) <= width:
        return s
    return s[:max(0, width - 1)] + "…"

def fit_line(s, max_width):
    """Ensure a string with ANSI does not exceed max_width visible chars."""
    if vis_len(s) <= max_width:
        return s
    curr_len = 0
    res = []
    for token in re.split(r"(\x1b\[[0-9;]*[a-zA-Z])", s):
        if not token:
            continue
        if token.startswith("\x1b["):
            res.append(token)
        else:
            needed = max_width - curr_len
            if needed <= 0:
                break
            if len(token) > needed:
                res.append(token[:max(0, needed - 1)] + "…")
                curr_len += needed
                break
            else:
                res.append(token)
                curr_len += len(token)
    res.append(RESET)
    return "".join(res)


def wrap_text(text, width, indent=0):
    """Wrap plain text to lines of given width."""
    prefix = " " * indent
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        for line in textwrap.wrap(para, max(10, width - indent)) or [""]:
            lines.append(prefix + line)
    return lines

def colorize_diff(diff):
    """Return list of colored diff lines."""
    out = []
    for line in diff.split("\n"):
        if line.startswith("+++") or (line.startswith("+") and not line.startswith("+++")):
            out.append(C_DIFF_A + line + RESET)
        elif line.startswith("---") or (line.startswith("-") and not line.startswith("---")):
            out.append(C_DIFF_D + line + RESET)
        elif line.startswith("@@"):
            out.append(C_CYAN + line + RESET)
        else:
            out.append(C_DIFF_N + line + RESET)
    return out

# ── Code Syntax Highlighting ──────────────────────────────────────────────────

_FORMATTER = TerminalTrueColorFormatter(style="monokai")

def highlight_code(code, lang=""):
    code = code.rstrip("\n")
    if not code:
        return [""]
    lexer = None
    if lang:
        try:
            lexer = get_lexer_by_name(lang.strip().lower(), stripall=True)
        except Exception:
            pass
    if lexer is None:
        try:
            lexer = guess_lexer(code)
        except Exception:
            pass
    if lexer is not None:
        try:
            res = pygments.highlight(code, lexer, _FORMATTER)
            return res.rstrip("\n").split("\n")
        except Exception:
            pass
    return [line for line in code.split("\n")]

# ── Splash ────────────────────────────────────────────────────────────────────

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
        ("model",       model_name, "/model to inspect"),
        ("directory",   cwd,        ""),
        ("commands",    "/help for shortcuts & commands", "Tab autocomplete"),
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

def get_git_branch(cwd):
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=0.6
        )
        if res.returncode == 0:
            branch = res.stdout.strip()
            res2 = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=0.6
            )
            dirty = "*" if res2.stdout.strip() else ""
            return branch + dirty
    except Exception:
        pass
    return None

# ── Markdown ANSI renderer (Clean, unboxed, original colors) ───────────────────

def render_markdown_ansi(text, width, indent=2):
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
                parts.append(BOLD + C_USER + render_inline(node.get("children", [])) + RESET)
            elif t == "emphasis":
                parts.append(ITALIC + C_AI + render_inline(node.get("children", [])) + RESET)
            elif t == "strikethrough":
                parts.append(ansi(9) + C_DIM + render_inline(node.get("children", [])) + RESET)
            elif t == "link":
                parts.append(C_CYAN + render_inline(node.get("children", [])) + RESET)
            else:
                if "children" in node:
                    parts.append(render_inline(node["children"]))
                elif "raw" in node:
                    parts.append(C_AI + node["raw"])
        return "".join(parts)

    def plain(children):
        return strip_ansi(render_inline(children))

    def render_table(node):
        head = None
        rows = []
        for child in node.get("children", []):
            ct = child.get("type")
            if ct == "table_head":
                head = [render_inline(c.get("children", [])) for c in child.get("children", [])]
            elif ct == "table_body":
                for r in child.get("children", []):
                    rows.append([render_inline(c.get("children", [])) for c in r.get("children", [])])

        all_r = ([head] if head else []) + rows
        if not all_r:
            return

        ncols = max(len(r) for r in all_r)
        for r in all_r:
            while len(r) < ncols:
                r.append("")

        col_w = [0] * ncols
        for r in all_r:
            for j, c in enumerate(r):
                col_w[j] = max(col_w[j], vis_len(c))

        top_b = pad + C_RULE_C + "┌" + "┬".join("─" * (w + 2) for w in col_w) + "┐" + RESET
        mid_b = pad + C_RULE_C + "├" + "┼".join("─" * (w + 2) for w in col_w) + "┤" + RESET
        bot_b = pad + C_RULE_C + "└" + "┴".join("─" * (w + 2) for w in col_w) + "┘" + RESET

        out.append(top_b)
        if head:
            cells = []
            for j, c in enumerate(head):
                p = " " * max(0, col_w[j] - vis_len(c))
                cells.append(f" {BOLD}{C_USER}{c}{RESET}{p} ")
            out.append(pad + C_RULE_C + "│" + (C_RULE_C + "│").join(cells) + C_RULE_C + "│" + RESET)
            out.append(mid_b)

        for r in rows:
            cells = []
            for j, c in enumerate(r):
                p = " " * max(0, col_w[j] - vis_len(c))
                cells.append(f" {C_AI}{c}{RESET}{p} ")
            out.append(pad + C_RULE_C + "│" + (C_RULE_C + "│").join(cells) + C_RULE_C + "│" + RESET)
        out.append(bot_b)
        out.append("")

    def render_block(node, list_depth=0, ordered=False, counter=None):
        t = node.get("type")
        if t == "blank_line":
            out.append("")
        elif t == "thematic_break":
            out.append(pad + C_RULE_C + "─" * inner_w + RESET)
        elif t == "heading":
            raw_txt = plain(node.get("children", []))
            level = node.get("attrs", {}).get("level", 1)
            hc = C_H1 if level == 1 else (C_H2 if level == 2 else C_H3)
            out.append(pad + hc + BOLD + raw_txt + RESET)
        elif t == "block_code":
            lang = ((node.get("attrs") or {}).get("info") or "").strip()
            out.append(pad + C_RULE_C + "─" * min(inner_w, 60) + RESET)
            if lang:
                out.append(pad + C_FENCE + BOLD + " " + lang + " " + RESET)
            raw_code = node.get("raw", "").rstrip("\n")
            code_lines = highlight_code(raw_code, lang)
            for code_line in code_lines:
                while len(strip_ansi(code_line)) > inner_w - 4:
                    out.append(pad + C_FENCE_BG + "  " + code_line[:inner_w-4] + RESET)
                    code_line = "  " + code_line[inner_w-4:]
                out.append(pad + C_FENCE_BG + "  " + code_line + RESET)
            out.append(pad + C_RULE_C + "─" * min(inner_w, 60) + RESET)
        elif t == "table":
            render_table(node)
        elif t == "block_quote":
            q_text = ""
            for child in node.get("children", []):
                q_text += plain(child.get("children", [])) + "\n"
            q_text = q_text.strip()

            alert_tag = None
            for tag, (name, col) in {
                "[!NOTE]": ("Note", C_CYAN),
                "[!TIP]": ("Tip", C_OK),
                "[!IMPORTANT]": ("Important", C_TOOL_P),
                "[!WARNING]": ("Warning", C_WARN),
                "[!CAUTION]": ("Caution", C_ERR),
            }.items():
                if q_text.startswith(tag):
                    alert_tag = (tag, name, col)
                    break

            if alert_tag:
                tag, name, col = alert_tag
                body = q_text[len(tag):].strip()
                out.append(pad + col + f"▌ {BOLD}{name}:{RESET}")
                for wl in textwrap.wrap(body, max(10, inner_w - 4)) or [""]:
                    out.append(pad + col + "▌ " + RESET + C_AI + wl + RESET)
            else:
                for line in q_text.split("\n"):
                    for wl in textwrap.wrap(line, max(10, inner_w - 4)) or [""]:
                        out.append(pad + C_RULE_C + "▌ " + RESET + C_QUOTE + wl + RESET)
        elif t == "task_list_item":
            checked = node.get("attrs", {}).get("checked", False)
            raw_txt = plain(node.get("children", []))
            cb = f"{BOLD}{C_OK}[✓]{RESET}" if checked else f"{C_DIM}[ ]{RESET}"
            words = textwrap.wrap(raw_txt, max(10, inner_w - 6)) or [""]
            for j, wl in enumerate(words):
                if j == 0:
                    out.append(pad + f"  {cb} {C_AI}{wl}{RESET}")
                else:
                    out.append(pad + f"      {C_AI}{wl}{RESET}")
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
            words     = textwrap.wrap(raw_txt, avail) or [""]
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
            raw_txt = plain(node.get("children", []))
            for wl in textwrap.wrap(raw_txt, inner_w) or [""]:
                out.append(pad + C_AI + wl + RESET)
            out.append("")
        else:
            for child in node.get("children", []):
                render_block(child)

    md_parse = mistune.create_markdown(renderer=None, plugins=["table", "task_lists", "strikethrough"])
    try:
        ast = md_parse(text)
    except Exception:
        for line in text.split("\n"):
            for wl in textwrap.wrap(line, inner_w) or [""]:
                out.append(pad + C_AI + wl + RESET)
        return out

    for node in (ast or []):
        render_block(node)

    return out

# ── Conversation Model ────────────────────────────────────────────────────────

class ToolNode:
    def __init__(self, tool_id, name, detail):
        self.tool_id = tool_id
        self.name = name
        self.detail = detail
        self.logs = []            # (stream, text)
        self.done = False
        self.error = False
        self.result_text = None
        self.start_time = time.time()
        self.end_time = None
        self.duration = None
        self.expanded = False

    def finish(self, result_text):
        self.done = True
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time
        self.result_text = result_text
        if result_text and isinstance(result_text, str):
            low = result_text.lower()
            if '"error":' in low or "error:" in low or '"failed":' in low:
                self.error = True

    def elapsed_str(self):
        t = self.duration if self.duration is not None else (time.time() - self.start_time)
        if t < 60:
            return f"{t:.1f}s"
        return f"{int(t // 60)}m {int(t % 60)}s"

class Turn:
    def __init__(self, user_text, is_system=False):
        self.user_text = user_text
        self.is_system = is_system
        self.blocks = []
        self.ai_lines = []
        self.thinking_chunks = []
        self.tool_nodes = {}
        self.tool_order = []

# ── Screen Renderer ───────────────────────────────────────────────────────────

class Screen:
    def __init__(self):
        self.turns = []
        self.cur_turn = None
        self.status_text = ""
        self.status_color = C_STATUS
        self.input_text = ""
        self.input_cursor = 0
        self.scroll_offset = 0
        self.spinner_i = 0
        self.busy = False
        self.thinking = False
        self.thinking_start = 0.0
        self.compact_mode = True
        self.history_badge = ""
        self.plan_mode = False

        self.active_tool_name = ""
        self.active_tool_detail = ""
        self.active_tool_start = 0.0
        self.active_thinking = ""

        self._lines_cache = []
        self._cache_dirty = True
        self._w = 80
        self._h = 24
        self._convo_h = 20

        self._question_state = None
        self._edit_state = None
        self._git_branch = None
        self._git_check = 0.0

        self._lock = threading.Lock()
        self._write(smcup() + enable_mouse() + hide_cursor() + BG_MAIN + ed(2))
        self._update_size()

    def _write(self, s):
        sys.stdout.write(s)
        sys.stdout.flush()

    def _update_size(self):
        w, h = terminal_size()
        self._w = w
        self._h = h
        # Reserve: status(1) + border-top(1) + input(1) + border-bot(1) = 4
        self._convo_h = max(4, h - 4)
        self._cache_dirty = True

    def _check_git(self):
        now = time.time()
        if now - self._git_check > 3.0:
            self._git_check = now
            threading.Thread(target=self._query_git, daemon=True).start()

    def _query_git(self):
        b = get_git_branch(os.getcwd())
        with self._lock:
            if b != self._git_branch:
                self._git_branch = b
                self._cache_dirty = True

    def append_thinking(self, text):
        with self._lock:
            if self.cur_turn is not None:
                self.cur_turn.thinking_chunks.append(text)
            self.active_thinking = text.strip().replace("\n", " ")[:60]
            self.thinking = True
            self._cache_dirty = True

    def set_active_tool(self, name, detail):
        with self._lock:
            self.active_tool_name = name
            self.active_tool_detail = detail
            self.active_tool_start = time.time()
            self.thinking = False
            self.active_thinking = ""
            self._cache_dirty = True

    def finish_active_tool(self):
        with self._lock:
            self.active_tool_name = ""
            self.active_tool_detail = ""
            self._cache_dirty = True

    # ── Full Redraw ───────────────────────────────────────────────

    def redraw(self):
        with self._lock:
            self._redraw_locked()

    def _redraw_locked(self):
        self._update_size()
        self._check_git()
        buf = []
        # Hide cursor immediately during screen generation to eliminate cursor drift/jitter
        buf.append(hide_cursor())
        buf.append(BG_MAIN)

        max_line_w = max(10, self._w - 1)

        # Convo area fills screen from row 1 to convo_h
        convo_start = 1
        lines = self._get_lines()
        visible_start = max(0, len(lines) - self._convo_h - self.scroll_offset)
        visible = lines[visible_start: visible_start + self._convo_h]

        for i in range(self._convo_h):
            row = convo_start + i
            buf.append(cup(row, 1) + el())
            if i < len(visible):
                buf.append(fit_line(visible[i], max_line_w))
        # Status bar in real time
        status_row = convo_start + self._convo_h
        git_str = f"  │  git: {self._git_branch}" if self._git_branch else ""
        plan_str = "  │  PLAN MODE" if self.plan_mode else ""
        spinner_f = SPINNER[self.spinner_i % len(SPINNER)]

        if self.active_tool_name:
            dur = time.time() - self.active_tool_start if self.active_tool_start else 0.0
            act = self.active_tool_name.lower()
            tc = TOOL_COLORS.get(act, C_TOOL)
            desc_map = {
                "bash": "bash working",
                "read": "reading",
                "edit": "editing",
                "grep": "searching",
                "glob": "finding files",
                "lsp": "lsp query",
                "agentjob": "agentjob running",
                "artifact": "artifact preview",
                "croncreate": "scheduling cron",
                "cronlist": "listing cron",
                "askuserquestion": "asking question",
            }
            action_label = desc_map.get(act, f"{self.active_tool_name} running")
            detail_trunc = truncate(self.active_tool_detail, 32) if self.active_tool_detail else ""
            if detail_trunc:
                left_st = f"  {spinner_f} {action_label}: {detail_trunc} ({dur:.1f}s)"
            else:
                left_st = f"  {spinner_f} {action_label} ({dur:.1f}s)"
            st_color = tc

        elif self.busy:
            dur = time.time() - self.thinking_start if self.thinking_start else 0.0
            if self.active_thinking:
                snippet = truncate(self.active_thinking, 32)
                left_st = f"  {spinner_f} thinking: {snippet} ({dur:.1f}s)"
            else:
                left_st = f"  {spinner_f} thinking through task… ({dur:.1f}s)"
            st_color = C_CYAN

        elif self.status_text:
            left_st = f"  {self.status_text}"
            st_color = self.status_color
        else:
            left_st = f"  ready"
            st_color = C_READY

        right_st = f"mistral-medium  │  /help  "
        st_content = f"{left_st}{git_str}{plan_str}"
        pad = max(1, max_line_w - vis_len(st_content) - len(right_st))
        full_status = f"{st_color}{st_content}{RESET}{' ' * pad}{C_DIM}{right_st}{RESET}"
        buf.append(cup(status_row, 1) + el() + BG_STATUS + fit_line(full_status, max_line_w) + RESET)

        # Input bar — top border / prompt line / bottom border (No box, just top and bottom border)
        border_top_row = status_row + 1
        input_row      = border_top_row + 1
        border_bot_row = input_row + 1

        border = C_BORDER + ("─" * max_line_w) + RESET
        buf.append(cup(border_top_row, 1) + el() + BG_MAIN + border)

        prompt = C_PROMPT + BOLD + "> " + RESET + BG_MAIN
        max_input = max(10, max_line_w - 4)
        display_text = self.input_text
        if len(display_text) > max_input:
            display_text = display_text[len(display_text) - max_input:]
        buf.append(cup(input_row, 1) + el() + BG_MAIN)
        buf.append(prompt + C_USER + display_text + RESET)

        # Bottom border with hints (strictly max_line_w to avoid triggering auto-wrap on last row)
        if self.busy:
            hint_text = "esc to interrupt"
            hint_pad  = max(0, max_line_w - len(hint_text))
            buf.append(
                cup(border_bot_row, 1) + el() + BG_MAIN
                + C_BORDER + "─" * hint_pad
                + C_DIM + hint_text
                + RESET
            )
        elif self.history_badge:
            hint_text = self.history_badge
            hint_pad  = max(0, max_line_w - len(hint_text))
            buf.append(
                cup(border_bot_row, 1) + el() + BG_MAIN
                + C_BORDER + "─" * hint_pad
                + C_CYAN + hint_text
                + RESET
            )
        elif self.input_text.startswith("/"):
            matched = [c[0] for c in COMMANDS if c[0].startswith(self.input_text.lower())]
            hint_text = "  ".join(matched[:5]) + " (Tab autocomplete)"
            hint_pad  = max(0, max_line_w - len(hint_text))
            buf.append(
                cup(border_bot_row, 1) + el() + BG_MAIN
                + C_BORDER + "─" * hint_pad
                + C_CYAN + hint_text
                + RESET
            )
        else:
            buf.append(cup(border_bot_row, 1) + el() + BG_MAIN + border)

        # Position cursor at target column and only then reveal it
        cursor_col = 3 + min(self.input_cursor, max_input)
        buf.append(cup(input_row, cursor_col))
        buf.append(show_cursor())

        self._write("".join(buf))

    # ── Line Cache ────────────────────────────────────────────────

    def _get_lines(self):
        if not self._cache_dirty:
            return self._lines_cache
        lines = []
        w = max(10, self._w - 2)

        # Splash header
        splash = build_splash(w, MODEL_NAME, os.getcwd())
        for line in splash:
            lines.append(BG_MAIN + line + RESET)
        lines.append("")

        for turn in self.turns:
            # User message (clean, exact original colors, no borders)
            if turn.is_system:
                lines.append(f"  {C_CYAN}ℹ {turn.user_text}{RESET}")
                lines.append("")
            else:
                user_lines = wrap_text(turn.user_text, w - 4, indent=0)
                for l in user_lines:
                    lines.append("  " + C_USER + l + RESET)
                lines.append("")

            # Thinking trace (if any)
            if turn.thinking_chunks:
                full_thought = "".join(turn.thinking_chunks).strip()
                if full_thought:
                    for tl in wrap_text(full_thought, w - 6, indent=0):
                        lines.append(f"    {C_DIM}{ITALIC}💭 {tl}{RESET}")
                    lines.append("")

            # Tool nodes
            for tid in turn.tool_order:
                node = turn.tool_nodes[tid]
                tc = TOOL_COLORS.get(node.name.lower(), C_TOOL)
                spinner_f = SPINNER[self.spinner_i % len(SPINNER)]
                detail_s = f"({node.detail})" if node.detail else ""
                elapsed = node.elapsed_str()

                if not node.done:
                    status_s = f" {C_CYAN}[running {spinner_f} {elapsed}]{RESET}"
                elif node.error:
                    status_s = f" {C_ERR}[failed ✗ {elapsed}]{RESET}"
                else:
                    status_s = f" {C_OK}[done ✓ {elapsed}]{RESET}"

                # Header: ToolName(detail): [status] (fitted to terminal width)
                header_line = (
                    f"  {BOLD}{tc}{node.name}{RESET}"
                    f"{C_DIM}{detail_s}:{RESET}"
                    f"{status_s}"
                )
                lines.append(fit_line(header_line, w))


                # Logs indented beneath, with folding if long
                flat_logs = []
                for stream, log_text in node.logs:
                    for line in log_text.split("\n"):
                        if line:
                            flat_logs.append((stream, line))

                display_logs = flat_logs
                if node.done and self.compact_mode and not node.expanded and len(flat_logs) > 8:
                    display_logs = flat_logs[:2] + [("info", f"… [{len(flat_logs) - 4} lines hidden — press Tab or Ctrl+O to expand]")] + flat_logs[-2:]

                for stream, log_text in display_logs:
                    if stream == "info":
                        lc = C_DIM
                    elif stream == "stderr":
                        lc = C_STDERR
                    else:
                        lc = C_STDOUT
                    for ll in wrap_text(log_text, w - 12, indent=0):
                        lines.append(f"          {lc}{ll}{RESET}")

                if not node.done:
                    lines.append(f"          {C_DIM}{spinner_f}{RESET}")
                lines.append("")

            # AI text
            if turn.ai_lines:
                if turn is not self.cur_turn:
                    full_ai_text = "\n".join(turn.ai_lines)
                    for ll in render_markdown_ansi(full_ai_text, w - 2, indent=2):
                        lines.append(ll)
                else:
                    for al in turn.ai_lines:
                        for ll in wrap_text(al, w - 4, indent=0):
                            lines.append("  " + C_AI + ll + RESET)
                lines.append("")

            # Interactive Question State
            if turn is self.cur_turn and self._question_state:
                qs = self._question_state
                q = qs["questions"][qs["index"]]
                lines.append("  " + C_TOOL_Y + BOLD + "? " + q.get("header","") + RESET)
                lines.append("  " + C_AI + q["question"] + RESET)
                sel_idx = qs.get("selected_option", 0)
                for i, opt in enumerate(q.get("options", [])):
                    is_sel = (i == sel_idx)
                    cursor = f"{BOLD}{C_TOOL_Y}❯{RESET} " if is_sel else "  "
                    num = f"[{i + 1}]"
                    lbl = opt.get("label", "")
                    desc = opt.get("description", "")
                    if is_sel:
                        lines.append(f"    {cursor}{BOLD}{C_TOOL_Y}{num} {lbl}{RESET} — {C_USER}{desc}{RESET} {C_TOOL_Y}(selected){RESET}")
                    else:
                        lines.append(f"    {cursor}{C_DIM}{num}{RESET} {C_AI}{lbl}{RESET} — {C_DIM}{desc}{RESET}")
                lines.append("  " + C_DIM + "↑/↓ select • 1-9 choose • Enter confirm • Tab custom ↓" + RESET)
                lines.append("")

            # Interactive Edit approval
            if turn is self.cur_turn and self._edit_state:
                es = self._edit_state
                lines.append("  " + C_TOOL_P + BOLD + "✎ Edit approval: " + RESET + C_AI + es["path"] + RESET)
                diff_lines = colorize_diff(es["diff"])
                for dl in diff_lines[:30]:
                    lines.append("    " + dl)
                if len(diff_lines) > 30:
                    lines.append(f"    {C_DIM}… [{len(diff_lines) - 30} more lines]…{RESET}")
                lines.append("  " + C_DIM + "Type 'y' to approve, 'n' to cancel (or press y/n) ↓" + RESET)
                lines.append("")

        self._lines_cache = lines
        self._cache_dirty = False
        return lines

    # ── State Mutators ────────────────────────────────────────────

    def set_status(self, text, color=None):
        with self._lock:
            self.status_text = text
            self.status_color = color or C_STATUS
            self._cache_dirty = True

    def set_busy(self, busy):
        with self._lock:
            self.busy = busy
            self.thinking = busy
            if busy:
                self.thinking_start = time.time()
            else:
                self.thinking_start = 0.0
            self._cache_dirty = True

    def tick_spinner(self):
        with self._lock:
            self.spinner_i += 1
            has_active_tool = any(
                not node.done
                for turn in self.turns
                for node in turn.tool_nodes.values()
            )
            if has_active_tool or self.thinking:
                self._cache_dirty = True

    def new_turn(self, user_text, is_system=False):
        with self._lock:
            t = Turn(user_text, is_system=is_system)
            self.turns.append(t)
            self.cur_turn = t
            self.scroll_offset = 0
            self._cache_dirty = True
        return t

    def add_tool_node(self, tool_id, name, detail):
        with self._lock:
            if self.cur_turn is None:
                return
            node = ToolNode(tool_id, name, detail)
            node.expanded = not self.compact_mode
            self.cur_turn.tool_nodes[tool_id] = node
            self.cur_turn.tool_order.append(tool_id)
            self._cache_dirty = True

    def append_tool_log(self, tool_id, stream, text):
        with self._lock:
            if self.cur_turn is None:
                return
            node = self.cur_turn.tool_nodes.get(tool_id)
            if node:
                node.logs.append((stream, text))
                self._cache_dirty = True

    def finish_tool_node(self, tool_id, result_text):
        with self._lock:
            if self.cur_turn is None:
                return
            node = self.cur_turn.tool_nodes.get(tool_id)
            if node:
                node.finish(result_text)
                self._cache_dirty = True

    def toggle_tool_expand(self):
        with self._lock:
            self.compact_mode = not self.compact_mode
            for turn in self.turns:
                for node in turn.tool_nodes.values():
                    node.expanded = not self.compact_mode
            self._cache_dirty = True

    def append_ai_text(self, text):
        with self._lock:
            if self.cur_turn is None:
                return
            self.thinking = False
            self.cur_turn.ai_lines.append(text)
            self._cache_dirty = True

    def end_turn(self):
        with self._lock:
            self.cur_turn = None
            self._question_state = None
            self._edit_state = None
            self.busy = False
            self.thinking = False
            self.active_tool_name = ""
            self.active_tool_detail = ""
            self.active_thinking = ""
            self._cache_dirty = True

    def set_question(self, state):
        with self._lock:
            self._question_state = state
            self._cache_dirty = True

    def set_edit(self, state):
        with self._lock:
            self._edit_state = state
            self._cache_dirty = True

    def scroll_up(self, n=3):
        with self._lock:
            lines = self._get_lines()
            max_scroll = max(0, len(lines) - self._convo_h)
            self.scroll_offset = min(self.scroll_offset + n, max_scroll)

    def scroll_down(self, n=3):
        with self._lock:
            self.scroll_offset = max(0, self.scroll_offset - n)

    def input_insert(self, ch):
        with self._lock:
            self.input_text = (self.input_text[:self.input_cursor]
                               + ch
                               + self.input_text[self.input_cursor:])
            self.input_cursor += 1

    def input_backspace(self):
        with self._lock:
            if self.input_cursor > 0:
                self.input_text = (self.input_text[:self.input_cursor-1]
                                   + self.input_text[self.input_cursor:])
                self.input_cursor -= 1

    def input_clear(self):
        with self._lock:
            self.input_text = ""
            self.input_cursor = 0
            self.history_badge = ""

    def input_left(self):
        with self._lock:
            self.input_cursor = max(0, self.input_cursor - 1)

    def input_right(self):
        with self._lock:
            self.input_cursor = min(len(self.input_text), self.input_cursor + 1)

    def input_home(self):
        with self._lock:
            self.input_cursor = 0

    def input_end(self):
        with self._lock:
            self.input_cursor = len(self.input_text)

    def take_input(self):
        with self._lock:
            text = self.input_text
            self.input_text = ""
            self.input_cursor = 0
            self.history_badge = ""
        return text

    def cleanup(self):
        self._write(disable_mouse() + show_cursor() + rmcup() + RESET)

# ── Input reading ─────────────────────────────────────────────────────────────

def read_key(fd):
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        seq = ch
        while True:
            r, _, _ = select.select([fd], [], [], 0.005)
            if not r:
                break
            try:
                chunk = os.read(fd, 32)
                if not chunk:
                    break
                seq += chunk
            except Exception:
                break
            if seq.startswith(b"\x1b[<") and (seq.endswith(b"M") or seq.endswith(b"m")):
                break
            if seq.startswith(b"\x1b[M") and len(seq) >= 6:
                break
            if len(seq) >= 3 and seq[-1:] in b"aAbBcCdDhHfF~RzZ":
                break

        # SGR mouse event: \x1b[<cb;cx;cy(M|m)
        if seq.startswith(b"\x1b[<"):
            m = re.match(rb"^\x1b\[<(\d+);(\d+);(\d+)([Mm])", seq)
            if m:
                cb = int(m.group(1))
                if cb == 64:
                    return "WHEEL_UP"
                elif cb == 65:
                    return "WHEEL_DOWN"
                return "MOUSE_EVENT"

        # Standard X10/X11 mouse event: \x1b[M cb cx cy
        if seq.startswith(b"\x1b[M") and len(seq) >= 6:
            cb = seq[3]
            if cb == 96:
                return "WHEEL_UP"
            elif cb == 97:
                return "WHEEL_DOWN"
            return "MOUSE_EVENT"

        mapping = {
            b"\x1b[A": "UP",
            b"\x1bOA": "UP",
            b"\x1b[B": "DOWN",
            b"\x1bOB": "DOWN",
            b"\x1b[C": "RIGHT",
            b"\x1bOC": "RIGHT",
            b"\x1b[D": "LEFT",
            b"\x1bOD": "LEFT",
            b"\x1b[5~": "PGUP",
            b"\x1b[6~": "PGDN",
            b"\x1b[H": "HOME",
            b"\x1b[1~": "HOME",
            b"\x1b[F": "END",
            b"\x1b[4~": "END",
            b"\x1b[3~": "DELETE",
            b"\x1b[1;2A": "SHIFT_UP",
            b"\x1b[1;2B": "SHIFT_DOWN",
            b"\x1b[Z": "SHIFT_TAB",
        }
        if seq in mapping:
            return mapping[seq]
        if seq == b"\x1b":
            return "ESC"
        return f"ESC_SEQ:{seq.hex()}"

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
        b"\t":   "TAB",
        b"\x0f": "CTRL_O",
        b"\x10": "CTRL_P",
        b"\x0e": "CTRL_N",
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
        self._tool_active = False
        self._question_state = None
        self._edit_state = None
        self._interrupted = False
        self._running = True
        self.start_time = datetime.now()

        # Command history
        self.history = []
        self.history_index = -1
        self.saved_input = ""

    def start(self):
        self._start_worker()
        threading.Thread(target=self._spinner_loop, daemon=True).start()
        threading.Thread(target=self._protocol_loop, daemon=True).start()
        try:
            signal.signal(signal.SIGWINCH, lambda s, f: self.screen.redraw())
        except Exception:
            pass
        self.screen.set_status("", C_STATUS)
        self.screen.redraw()
        self._input_loop()

    def _start_worker(self):
        self.proc = subprocess.Popen(
            [sys.executable, WORKER] + sys.argv[1:],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open("/tmp/priya_worker.log", "w"),
            text=True, bufsize=1,
        )
        threading.Thread(target=reader_thread,
                         args=(self.proc, self.raw_q), daemon=True).start()

    def _spinner_loop(self):
        while self._running:
            should_redraw = False
            with self.screen._lock:
                has_active = any(
                    not node.done
                    for turn in self.screen.turns
                    for node in turn.tool_nodes.values()
                )
                if has_active or self.screen.thinking or self.screen.busy or bool(self.screen.active_tool_name):
                    self.screen.spinner_i += 1
                    self.screen._cache_dirty = True
                    should_redraw = True
            if should_redraw:
                self.screen.redraw()
            time.sleep(0.08)

    def _protocol_loop(self):
        deferred_finishes = []
        thinking_hidden = False

        while self._running:
            item = self.raw_q.get()
            if item is SENTINEL:
                self.screen.set_status("worker closed — restarting…", C_WARN)
                time.sleep(0.5)
                self._start_worker()
                continue

            line = item.strip()
            if not line:
                continue

            if line.startswith("<<THINKING>>"):
                try:
                    p = json.loads(line[len("<<THINKING>>"):])
                    self.screen.append_thinking(p.get("text", ""))
                    self.screen.redraw()
                except Exception:
                    pass
                continue

            if line.startswith("<<TOOL_START>>"):
                try:
                    p = json.loads(line[len("<<TOOL_START>>"):])
                    tid = p.get("id", f"t{id(p)}")
                    self.screen.add_tool_node(tid, p["name"], p.get("detail",""))
                    self.screen.set_active_tool(p["name"], p.get("detail",""))
                    self._tool_active = True
                    self.screen.redraw()
                except Exception:
                    pass
                continue

            if line.startswith("<<TOOL_LOG>>"):
                try:
                    p = json.loads(line[len("<<TOOL_LOG>>"):])
                    self.screen.append_tool_log(p["id"], p.get("stream","stdout"), p["text"])
                    self.screen.redraw()
                except Exception:
                    pass
                continue

            if line.startswith("<<TOOL_END>>"):
                try:
                    p = json.loads(line[len("<<TOOL_END>>"):])
                    tid = p.get("id")
                    name = p.get("name", "")
                    res = p.get("result", {})
                    if name == "EnterPlanMode" or (isinstance(res, dict) and res.get("plan_mode") is True):
                        self.screen.plan_mode = True
                    elif name == "ExitPlanMode" or (isinstance(res, dict) and res.get("plan_mode") is False):
                        self.screen.plan_mode = False

                    result_text = json.dumps(res, indent=2)
                    deferred_finishes.append((tid, result_text))
                    self.screen.finish_active_tool()
                    self.screen.redraw()
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
                        "selected_option": 0,
                    }
                    self.screen.set_question(self._question_state)
                    self.screen.set_status("? Waiting for your answer", C_TOOL_Y)
                    self.screen.redraw()
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
                    self.screen.set_status(f"✎ Review edit for {p.get('path', '')}", C_TOOL_P)
                    self.screen.redraw()
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
                        self.screen.redraw()
                except Exception:
                    pass
                continue

            if line.startswith("<<AGENTJOB_RESULT>>"):
                continue

            if line == "<<END>>":
                for tid, rt in deferred_finishes:
                    self.screen.finish_tool_node(tid, rt)
                deferred_finishes.clear()
                self._busy = False
                self._tool_active = False
                self._interrupted = False
                self.screen.finish_active_tool()
                self.screen.set_busy(False)
                self.screen.end_turn()
                self.screen.set_status("ready", C_READY)
                thinking_hidden = False
                self.screen.redraw()
                continue

            # Streamed text
            if not thinking_hidden:
                thinking_hidden = True
                self.screen.thinking = False
            for tid, rt in deferred_finishes:
                self.screen.finish_tool_node(tid, rt)
            deferred_finishes.clear()
            self.screen.append_ai_text(line)
            self.screen.redraw()

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
            if s.input_text:
                s.input_clear()
                self.history_index = -1
                s.redraw()
            else:
                self._running = False
            return

        if key == "CTRL_L":
            s.redraw()
            return

        if key == "CTRL_O":
            s.toggle_tool_expand()
            s.redraw()
            return

        if key == "TAB":
            if s.input_text.startswith("/"):
                matched = [c[0] for c in COMMANDS if c[0].startswith(s.input_text.lower())]
                if matched:
                    with s._lock:
                        s.input_text = matched[0]
                        s.input_cursor = len(s.input_text)
                    s.redraw()
                    return
            s.toggle_tool_expand()
            s.redraw()
            return

        if key == "WHEEL_UP":
            s.scroll_up(3)
            s.redraw()
            return

        if key == "WHEEL_DOWN":
            s.scroll_down(3)
            s.redraw()
            return

        if key == "MOUSE_EVENT":
            return

        if key == "SHIFT_UP":
            s.scroll_up(3)
            s.redraw()
            return

        if key == "SHIFT_DOWN":
            s.scroll_down(3)
            s.redraw()
            return

        if key == "PGUP":
            s.scroll_up(s._convo_h - 2)
            s.redraw()
            return

        if key == "PGDN":
            s.scroll_down(s._convo_h - 2)
            s.redraw()
            return

        if key in ("UP", "CTRL_P"):
            if self._question_state:
                qs = self._question_state
                q = qs["questions"][qs["index"]]
                opts = q.get("options", [])
                with s._lock:
                    qs["selected_option"] = max(0, qs.get("selected_option", 0) - 1)
                    s._cache_dirty = True
                s.redraw()
                return

            # If user has scrolled up, UP arrow scrolls conversation further
            if s.scroll_offset > 0:
                s.scroll_up(3)
                s.redraw()
                return

            if not s.busy:
                if self.history:
                    if self.history_index == -1:
                        self.saved_input = s.input_text
                        self.history_index = len(self.history) - 1
                    elif self.history_index > 0:
                        self.history_index -= 1

                    if 0 <= self.history_index < len(self.history):
                        with s._lock:
                            s.input_text = self.history[self.history_index]
                            s.input_cursor = len(s.input_text)
                            s.history_badge = f"history {self.history_index + 1}/{len(self.history)}"
                        s.redraw()
                        return

            s.scroll_up(3)
            s.redraw()
            return

        if key in ("DOWN", "CTRL_N"):
            if self._question_state:
                qs = self._question_state
                q = qs["questions"][qs["index"]]
                opts = q.get("options", [])
                with s._lock:
                    qs["selected_option"] = min(len(opts) - 1, qs.get("selected_option", 0) + 1)
                    s._cache_dirty = True
                s.redraw()
                return

            # If user is scrolled up into conversation, DOWN arrow scrolls down
            if s.scroll_offset > 0:
                s.scroll_down(3)
                s.redraw()
                return

            if self.history_index != -1:
                self.history_index += 1
                if self.history_index >= len(self.history):
                    self.history_index = -1
                    with s._lock:
                        s.input_text = self.saved_input
                        s.input_cursor = len(s.input_text)
                        s.history_badge = ""
                else:
                    with s._lock:
                        s.input_text = self.history[self.history_index]
                        s.input_cursor = len(s.input_text)
                        s.history_badge = f"history {self.history_index + 1}/{len(self.history)}"
                s.redraw()
                return
            s.scroll_down(3)
            s.redraw()
            return

        if key == "ESC":
            if self._edit_state:
                self._submit_edit_approval(False)
                return
            if self._question_state:
                self._cancel_question()
                return
            if self._busy:
                self._do_interrupt()
                return
            if s.input_text:
                s.input_clear()
                self.history_index = -1
                s.redraw()
            return

        if key == "CTRL_U":
            s.input_clear()
            self.history_index = -1
            s.redraw()
            return

        if key == "LEFT":
            s.input_left()
            s.redraw()
            return

        if key == "RIGHT":
            s.input_right()
            s.redraw()
            return

        if key == "HOME":
            s.input_home()
            s.redraw()
            return

        if key == "END":
            s.input_end()
            s.redraw()
            return

        if key == "BACKSPACE":
            s.input_backspace()
            s.redraw()
            return

        if key == "ENTER":
            self._do_submit()
            return

        if self._question_state and key in "123456789" and not s.input_text:
            idx = int(key) - 1
            qs = self._question_state
            q = qs["questions"][qs["index"]]
            opts = q.get("options", [])
            if 0 <= idx < len(opts):
                qs["selected_option"] = idx
                self._submit_question_choice()
                return

        if self._edit_state and not s.input_text:
            if key in ("y", "Y"):
                self._submit_edit_approval(True)
                return
            elif key in ("n", "N"):
                self._submit_edit_approval(False)
                return

        if len(key) == 1 and key.isprintable():
            s.input_insert(key)
            s.redraw()
            return

    def _do_submit(self):
        text = self.screen.take_input().strip()

        if self._question_state is not None:
            qs = self._question_state
            q = qs["questions"][qs["index"]]
            opts = q.get("options", [])
            if text:
                ans = text
            else:
                sel_idx = qs.get("selected_option", 0)
                ans = opts[sel_idx]["label"] if opts else ""
            self._submit_question_answer(ans)
            return

        if self._edit_state is not None:
            if not text:
                self._submit_edit_approval(True)
            else:
                approved = text.casefold() in ("y", "yes", "approve", "approved")
                self._submit_edit_approval(approved)
            return

        if not text:
            return

        if not self.history or self.history[-1] != text:
            self.history.append(text)
        self.history_index = -1
        self.saved_input = ""

        # Slash Commands
        cmd = text.split()[0].lower()
        if cmd == "/help":
            self._handle_cmd_help()
            return
        elif cmd == "/clear":
            self._handle_cmd_clear()
            return
        elif cmd == "/status":
            self._handle_cmd_status()
            return
        elif cmd == "/diff":
            self._handle_cmd_diff()
            return
        elif cmd == "/tools":
            self._handle_cmd_tools()
            return
        elif cmd == "/model":
            self._handle_cmd_model()
            return
        elif cmd == "/history":
            self._handle_cmd_history()
            return
        elif cmd == "/compact":
            self.screen.toggle_tool_expand()
            mode = "compact" if self.screen.compact_mode else "expanded"
            self.screen.set_status(f"tool logs set to {mode}", C_OK)
            self.screen.redraw()
            return
        elif cmd in ("/exit", "/quit", "q"):
            self._running = False
            return

        # Normal prompt to worker
        self._busy = True
        self._interrupted = False
        self.screen.new_turn(text)
        self.screen.set_busy(True)
        self.screen.set_status("", C_STATUS)
        threading.Thread(target=self._send_line, args=(text,), daemon=True).start()

    # ── Slash Command Handlers ────────────────────────────────────

    def _handle_cmd_help(self):
        turn = self.screen.new_turn("/help", is_system=True)
        help_md = """
### Priya Commands & Shortcuts

| Command | Action |
| :--- | :--- |
| `/help` | Show this guide |
| `/status` | Workspace, git & session status |
| `/diff` | Show unstaged git diff |
| `/clear` | Clear conversation history |
| `/tools` | List all 14 tools & descriptions |
| `/model` | Active model information |
| `/history` | Recent prompt history |
| `/compact` | Toggle tool logs compact mode |
| `/exit` | Exit Priya |

**Keys**: `Enter` send • `Esc` interrupt • `Tab` autocomplete/fold • `Ctrl+O` fold logs • `↑/↓` history/scroll • `Ctrl+C` quit
"""
        turn.ai_lines.append(help_md.strip())
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_status(self):
        turn = self.screen.new_turn("/status", is_system=True)
        uptime = datetime.now() - self.start_time
        up_s = f"{int(uptime.total_seconds() // 60)}m {int(uptime.total_seconds() % 60)}s"
        total_tools = sum(len(t.tool_nodes) for t in self.screen.turns)
        branch = self.screen._git_branch or "none"
        plan_s = "active" if self.screen.plan_mode else "inactive"
        compact_s = "compact" if self.screen.compact_mode else "expanded"

        status_md = f"""
### Priya Status

- **Model**: {MODEL_NAME}
- **Workspace**: {os.getcwd()}
- **Git branch**: {branch}
- **Session uptime**: {up_s}
- **Turns**: {len(self.screen.turns)}
- **Tool calls**: {total_tools}
- **Plan mode**: {plan_s}
- **Tool log mode**: {compact_s}
"""
        turn.ai_lines.append(status_md.strip())
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_diff(self):
        turn = self.screen.new_turn("/diff", is_system=True)
        try:
            res = subprocess.run(
                ["git", "diff"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3
            )
            diff_text = res.stdout.strip()
            if not diff_text:
                turn.ai_lines.append("> [!NOTE]\n> Working tree clean — no unstaged changes.")
            else:
                turn.ai_lines.append(f"```diff\n{diff_text}\n```")
        except Exception as e:
            turn.ai_lines.append(f"> [!CAUTION]\n> Git diff failed: {e}")
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_tools(self):
        turn = self.screen.new_turn("/tools", is_system=True)
        tools_md = """
### Tools Reference

| Tool | Description |
| :--- | :--- |
| `bash` | Run shell commands in foreground or background |
| `Read` | Read exact file contents and line ranges |
| `Edit` | Surgical search-and-replace with diff approval |
| `Glob` | File pattern search |
| `Grep` | Fast regex content search |
| `LSP` | Definitions, diagnostics & hover from language server |
| `artifact` | Interactive HTML preview server |
| `agentjob` | Background front-end implementation runner |
| `askUserQuestion`| Interactive multiple-choice prompts |
| `EnterPlanMode` | Read-only planning mode |
| `ExitPlanMode` | Resume change execution |
| `CronCreate` | Schedule session prompts |
| `CronList` | List scheduled cron jobs |
| `ListMcpResourcesTool` | Discover configured MCP server resources |
"""
        turn.ai_lines.append(tools_md.strip())
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_model(self):
        turn = self.screen.new_turn("/model", is_system=True)
        model_md = f"""
### Model Info

- **Model**: {MODEL_NAME}
- **Worker**: `live_cli.py` (asyncio event loop)
- **Protocol**: Framing `<<TOOL_*>>`, `<<ASK_*>>`, `<<EDIT_*>>`
"""
        turn.ai_lines.append(model_md.strip())
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_history(self):
        turn = self.screen.new_turn("/history", is_system=True)
        if not self.history:
            turn.ai_lines.append("> [!NOTE]\n> No prompt history yet.")
        else:
            items = "\n".join(f"{i + 1}. `{cmd}`" for i, cmd in enumerate(self.history[-15:]))
            turn.ai_lines.append(f"### Recent History\n\n{items}")
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_clear(self):
        with self.screen._lock:
            self.screen.turns.clear()
            self.screen.cur_turn = None
            self.screen.scroll_offset = 0
            self.screen._cache_dirty = True
        self.screen.set_status("cleared", C_OK)
        self.screen.redraw()

    def _submit_question_choice(self):
        qs = self._question_state
        if not qs:
            return
        q = qs["questions"][qs["index"]]
        sel_idx = qs.get("selected_option", 0)
        opts = q.get("options", [])
        ans = opts[sel_idx]["label"] if opts else ""
        self._submit_question_answer(ans)

    def _submit_question_answer(self, ans):
        qs = self._question_state
        if not qs:
            return
        qs["answers"].append(ans)
        qs["index"] += 1
        qs["selected_option"] = 0
        if qs["index"] < len(qs["questions"]):
            self.screen.set_question(qs)
        else:
            payload = {"id": qs["id"], "answers": qs["answers"]}
            self._question_state = None
            self.screen.set_question(None)
            self._send_line("<<ASK_USER_ANSWER>>" + json.dumps(payload))
        self.screen.redraw()

    def _cancel_question(self):
        if not self._question_state:
            return
        payload = {"id": self._question_state["id"], "cancelled": True}
        self._question_state = None
        self.screen.set_question(None)
        self._send_line("<<ASK_USER_ANSWER>>" + json.dumps(payload))
        self.screen.set_status("question cancelled", C_DIM)
        self.screen.redraw()

    def _submit_edit_approval(self, approved):
        if not self._edit_state:
            return
        payload = {"id": self._edit_state["id"], "approved": approved}
        self._edit_state = None
        self.screen.set_edit(None)
        self._send_line("<<EDIT_APPROVAL>>" + json.dumps(payload))
        self.screen.set_status("edit approved" if approved else "edit rejected", C_OK if approved else C_WARN)
        self.screen.redraw()

    def _do_interrupt(self):
        if not self._busy:
            return
        self._interrupted = True
        self._busy = False
        self.screen.set_busy(False)
        self.screen.set_status("⊘ Interrupted", C_DIM)
        self._send_line("<<PRIYA_INTERRUPT>>")
        self.screen.redraw()

    def _send_line(self, text):
        try:
            with self._stdin_lock:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.screen.set_status("worker pipe broken", C_ERR)

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
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
