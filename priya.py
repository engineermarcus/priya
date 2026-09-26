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

from chat_db import ChatDB
from env_manager import (
    load_project_env,
    save_api_key,
    get_api_keys_status,
    get_onboarding_instructions,
)

# ── Constants ─────────────────────────────────────────────────────────────────

DIR = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(DIR, "live_cli.py")
MODEL_NAME = "magistral-medium-latest"
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
def enable_mouse():  return CSI + "?1000l" + CSI + "?1006l" + CSI + "?1007h" + CSI + "?2004h"
def disable_mouse(): return CSI + "?1007l" + CSI + "?2004l"
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
    "write":                C_TOOL_P,
    "read":                 C_CYAN,
    "grep":                 C_WARN,
    "glob":                 C_WARN,
    "lsp":                  C_TOOL_P,
    "websearch":            C_CYAN,
    "webfetch":             C_TOOL_B,
    "taskcreate":           C_TOOL_Y,
    "tasklist":             C_TOOL_Y,
    "taskget":              C_TOOL_Y,
    "taskupdate":           C_TOOL_Y,
    "taskstop":             C_TOOL_Y,
    "agentjob":             C_HEAD,
    "croncreate":           C_WARN,
    "cronlist":             C_WARN,
    "crondelete":           C_WARN,
    "listmcpresourcestool": C_TOOL,
    "readmcpresourcetool":  C_TOOL,
    "enterplanmode":        C_WARN,
    "exitplanmode":         C_WARN,
    "askuserquestion":      C_TOOL_Y,
    "monitor":              C_WARN,
    "pushnotification":     C_TOOL_Y,
    "remotetrigger":        C_TOOL_B,
    "reportfindings":       C_CYAN,
    "schedulewakeup":       C_WARN,
    "sendmessage":          C_TOOL_Y,
    "senduserfile":         C_CYAN,
    "shareonboardingguide": C_TOOL_P,
    "skill":                C_HEAD,
    "taskoutput":           C_TOOL_Y,
    "todowrite":            C_TOOL_P,
    "toolsearch":           C_CYAN,
    "waitformcpservers":    C_TOOL,
    "workflow":             C_HEAD,
}

COMMANDS = [
    ("/help", "Show commands, keybindings & guide"),
    ("/status", "Display workspace, git & session status"),
    ("/diff", "Show git diff of unstaged changes"),
    ("/clear", "Clear conversation screen"),
    ("/tools", "List all available Priya tools"),
    ("/models", "Switch active model (Mistral / Gemini 3.8 Flash)"),
    ("/model", "View active model information"),
    ("/delete", "Pick and delete a chat from project history"),
    ("/onboarding", "Configure or switch API keys (Mistral / Gemini)"),
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

def format_log_line(stream, line, is_diff=False):
    """Format and colorize a single tool output line."""
    if is_diff:
        if line.startswith("+++") or (line.startswith("+") and not line.startswith("+++")):
            return C_DIFF_A + line + RESET
        elif line.startswith("---") or (line.startswith("-") and not line.startswith("---")):
            return C_DIFF_D + line + RESET
        elif line.startswith("@@"):
            return C_CYAN + line + RESET
    if stream == "stderr":
        return C_STDERR + line + RESET
    elif stream == "info":
        return C_DIM + line + RESET
    else:
        return C_STDOUT + line + RESET

def format_path(p):
    if not isinstance(p, str):
        return p
    home = os.path.expanduser("~")
    if home and home in p:
        return p.replace(home, "~")
    return p

def get_tool_summary(name, detail, result_text):
    if not result_text:
        return None
    try:
        r = json.loads(result_text) if isinstance(result_text, str) else result_text
    except Exception:
        return None
    if not isinstance(r, dict):
        return None

    if r.get("error"):
        err = str(r["error"]).split("\n")[0]
        return f"Failed: {err}"

    act = name.lower()

    if act == "read":
        content = r.get("content", "")
        lines = len(content.splitlines()) if isinstance(content, str) else 0
        return f"Read {lines} {'line' if lines == 1 else 'lines'}."

    elif act == "edit":
        if r.get("approved") is False:
            return "Edit cancelled."
        diff = r.get("diff", "")
        if diff:
            added = len([l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")])
            removed = len([l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")])
            if added > 0 and removed == 0:
                return f"Added {added} {'line' if added == 1 else 'lines'}."
            elif removed > 0 and added == 0:
                return f"Removed {removed} {'line' if removed == 1 else 'lines'}."
            elif added > 0 and removed > 0:
                return f"Added {added}, removed {removed} lines."
            else:
                return "Updated file."
        return "Updated file."

    elif act == "write":
        lines = r.get("lines_written")
        if lines is None and "content" in r:
            lines = len(str(r["content"]).splitlines())
        if lines is not None:
            return f"Wrote {lines} {'line' if lines == 1 else 'lines'}."
        return "File written."

    elif act == "glob":
        count = r.get("count", len(r.get("files", [])))
        return f"Found {count} {'file' if count == 1 else 'files'}."

    elif act == "grep":
        count = r.get("count", len(r.get("matches", [])))
        matches = r.get("matches", [])
        files = len(set(m.get("path") for m in matches if isinstance(m, dict))) if matches else 0
        if files > 1:
            return f"Found {count} matches across {files} files."
        return f"Found {count} {'match' if count == 1 else 'matches'}."

    elif act == "websearch":
        count = r.get("count", len(r.get("results", [])))
        return f"Found {count} results."

    elif act == "webfetch":
        chars = r.get("length", len(r.get("content", "")))
        code = r.get("status_code", 200)
        return f"Fetched {chars:,} characters (HTTP {code})."

    elif act == "taskcreate":
        tid = r.get("task", {}).get("id") or "task"
        return f"Created {tid}."

    elif act == "taskupdate":
        tid = r.get("task", {}).get("id") or "task"
        st = r.get("task", {}).get("status")
        return f"Updated {tid} ({st})." if st else f"Updated {tid}."

    elif act == "taskstop":
        return "Task cancelled."

    elif act == "tasklist":
        count = r.get("count", len(r.get("tasks", [])))
        return f"Loaded {count} tasks."

    elif act == "taskget":
        tid = r.get("task", {}).get("id")
        return f"Loaded {tid}."

    elif act == "listmcpresourcestool":
        count = r.get("count", len(r.get("resources", [])))
        return f"Found {count} MCP resources."

    elif act == "readmcpresourcetool":
        return "Read MCP resource."

    elif act == "enterplanmode":
        return "Entered Plan Mode (read-only)."

    elif act == "exitplanmode":
        return "Exited Plan Mode."

    elif act == "enterworktree":
        wt = r.get("worktree", "")
        return f"Entered worktree {format_path(wt)}."

    elif act == "exitworktree":
        return "Exited worktree."

    elif act == "croncreate":
        return "Scheduled prompt."

    elif act == "cronlist":
        count = len(r) if isinstance(r, list) else 0
        return f"Active scheduled jobs: {count}."

    elif act == "bash":
        exit_code = r.get("exit_code")
        if exit_code is not None and exit_code != 0:
            return f"Command exited with code {exit_code}."
        return None

    elif act == "monitor":
        pid = r.get("pid") or r.get("process_id")
        alive = r.get("running")
        st = "running" if alive else "stopped"
        return f"Process {pid} ({st})." if pid else f"Checked {r.get('count', 0)} processes."

    elif act == "pushnotification":
        return f"Notification delivered: {r.get('title')}."

    elif act == "remotetrigger":
        code = r.get("status_code", 200)
        s = r.get("elapsed_s", 0)
        return f"Triggered HTTP {code} in {s}s."

    elif act == "reportfindings":
        count = r.get("findings_count", 0)
        return f"Generated report ({count} {'finding' if count == 1 else 'findings'})."

    elif act == "schedulewakeup":
        s = r.get("delay_seconds", 0)
        return f"Wakeup scheduled in {s}s."

    elif act == "sendmessage":
        return f"Message sent to {r.get('target', 'recipient')}."

    elif act == "senduserfile":
        fn = r.get("filename") or os.path.basename(r.get("path", "file"))
        kb = round(r.get("size_bytes", 0) / 1024, 1)
        return f"Exported {fn} ({kb} KB)."

    elif act == "shareonboardingguide":
        p = format_path(r.get("path", "ONBOARDING.md"))
        return f"Created onboarding guide ({p})."

    elif act == "skill":
        if "skills" in r:
            return f"Found {r.get('count', len(r['skills']))} skills."
        return f"Executed {r.get('skill', 'skill')} (exit {r.get('exit_code', 0)})."

    elif act == "taskoutput":
        return f"Recorded output for {r.get('task_id', 'task')}."

    elif act == "todowrite":
        total = r.get("total", 0)
        comp = r.get("completed", 0)
        return f"Saved {total} todos ({comp} completed)."

    elif act == "toolsearch":
        count = r.get("count", len(r.get("matches", [])))
        return f"Found {count} matching {'tool' if count == 1 else 'tools'}."

    elif act == "waitformcpservers":
        ready = "ready" if r.get("ready") else "timed out"
        return f"MCP servers {ready}."

    elif act == "workflow":
        wid = r.get("workflow_id", "workflow")
        st = r.get("status", "done")
        steps = r.get("completed_steps", 0)
        total = r.get("total_steps", steps)
        return f"Workflow {wid} ({st}, {steps}/{total} steps)."

    return None

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

    while out and not out[-1].strip():
        out.pop()

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
        self.error = False
        if result_text:
            try:
                res = json.loads(result_text) if isinstance(result_text, str) else result_text
                if isinstance(res, dict):
                    if res.get("error"):
                        self.error = True
                    elif res.get("exit_code") is not None and res.get("exit_code") != 0:
                        self.error = True
                    elif res.get("failed") is True:
                        self.error = True
            except Exception:
                pass

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
        self.interrupted = False

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
        self.model_badge = "mistral-medium"
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
        self.suggestion_index = 0
        self._git_branch = None
        self._git_check = 0.0

        self._lock = threading.RLock()
        self._write(smcup() + enable_mouse() + hide_cursor() + BG_MAIN + ed(2))
        self._update_size()

    def set_model_badge(self, badge):
        with self._lock:
            self.model_badge = badge
            self._cache_dirty = True

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
                if (self.cur_turn.blocks 
                        and self.cur_turn.blocks[-1].get("type") == "thinking" 
                        and not self.cur_turn.blocks[-1].get("done")):
                    self.cur_turn.blocks[-1]["chunks"].append(text)
                else:
                    self.cur_turn.blocks.append({
                        "type": "thinking",
                        "chunks": [text],
                        "start_time": time.time(),
                        "end_time": None,
                        "done": False,
                    })
                    self.thinking_start = time.time()
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
        spinner_f = SPINNER[self.spinner_i % len(SPINNER)]

        if self.active_tool_name:
            dur = time.time() - self.active_tool_start if self.active_tool_start else 0.0
            act = self.active_tool_name.lower()
            tc = TOOL_COLORS.get(act, C_TOOL)
            desc_map = {
                "bash": "bash working",
                "read": "reading",
                "write": "writing",
                "edit": "editing",
                "grep": "searching",
                "glob": "finding files",
                "lsp": "lsp query",
                "websearch": "searching web",
                "webfetch": "fetching page",
                "taskcreate": "creating task",
                "tasklist": "listing tasks",
                "taskget": "getting task",
                "taskupdate": "updating task",
                "taskstop": "stopping task",
                "agentjob": "agentjob running",
                "artifact": "artifact preview",
                "croncreate": "scheduling cron",
                "cronlist": "listing cron",
                "listmcpresourcestool": "listing mcp",
                "readmcpresourcetool": "reading mcp",
                "askuserquestion": "asking question",
                "monitor": "monitoring process",
                "pushnotification": "pushing notification",
                "remotetrigger": "triggering webhook",
                "reportfindings": "generating report",
                "schedulewakeup": "scheduling wakeup",
                "sendmessage": "sending message",
                "senduserfile": "exporting file",
                "shareonboardingguide": "generating guide",
                "skill": "running skill",
                "taskoutput": "saving task output",
                "todowrite": "updating todos",
                "toolsearch": "searching tools",
                "waitformcpservers": "waiting for mcp",
                "workflow": "running workflow",
                "spawn_agent": "spawning agent",
                "send_input": "sending input",
                "wait_agent": "waiting for agent",
                "close_agent": "closing agent",
                "resume_agent": "resuming agent",
                "spawn_agents_on_csv": "batch spawning agents",
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
            scroll_hint = f" [+{self.scroll_offset}]" if self.scroll_offset > 0 else ""
            if self.active_thinking:
                snippet = truncate(self.active_thinking, 32)
                left_st = f"  {spinner_f} thinking: {snippet} ({dur:.1f}s){scroll_hint}"
            else:
                left_st = f"  {spinner_f} Generating… ({dur:.1f}s){scroll_hint}"
            st_color = C_CYAN

        elif self.scroll_offset > 0:
            left_st = f"  ↑ scrolled +{self.scroll_offset} lines  │  press ↓ to return"
            st_color = C_CYAN

        elif self.status_text and self.status_text.strip() != "ready":
            left_st = f"  {self.status_text}"
            st_color = self.status_color
        else:
            left_st = ""
            st_color = C_READY

        plan_str = "  │  PLAN MODE" if (self.plan_mode and left_st) else ("  PLAN MODE" if self.plan_mode else "")
        right_st = f"{self.model_badge}  │  /help  "
        st_content = f"{left_st}{plan_str}"
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
            hint_text = "↑/↓ iterate • Enter complete"
            hint_pad  = max(0, max_line_w - len(hint_text))
            buf.append(
                cup(border_bot_row, 1) + el() + BG_MAIN
                + C_BORDER + "─" * hint_pad
                + C_CYAN + hint_text
                + RESET
            )
        else:
            buf.append(cup(border_bot_row, 1) + el() + BG_MAIN + border)

        # ── Bottom-Left Dropdown Overlay (above status / input bar) ───
        if self._question_state:
            qs = self._question_state
            header = qs.get("header") or "Select Option"
            q_idx = qs.get("index", 0)
            questions_list = qs.get("questions", [])
            curr_q = questions_list[q_idx] if q_idx < len(questions_list) else {}
            q_text = curr_q.get("question", "")
            opts = curr_q.get("options", [])
            sel_idx = qs.get("selected_option", 0)

            box_w = min(max_line_w - 4, 76)
            content_w = max(10, box_w - 4)

            drop_lines = []
            total_q = len(questions_list)
            step_str = f" [Step {q_idx + 1}/{total_q}]" if total_q > 1 else ""
            title_text = f"┌─ {header}{step_str} "
            drop_lines.append(C_BORDER + title_text + "─" * max(0, box_w - vis_len(title_text) - 1) + "┐" + RESET)
            if q_text:
                q_disp = fit_line(q_text, content_w)
                q_pad = " " * max(0, content_w - vis_len(q_disp))
                drop_lines.append(C_BORDER + "│ " + RESET + C_AI + q_disp + RESET + q_pad + C_BORDER + " │" + RESET)
                drop_lines.append(C_BORDER + "├" + "─" * (box_w - 2) + "┤" + RESET)

            for i, opt in enumerate(opts):
                is_sel = (i == sel_idx)
                lbl = opt.get("label", "")
                desc = opt.get("description", "")
                if is_sel:
                    opt_str = f"❯ [{i + 1}] {lbl}"
                    opt_disp = fit_line(opt_str, content_w)
                    opt_pad = " " * max(0, content_w - vis_len(opt_disp))
                    drop_lines.append(C_BORDER + "│ " + RESET + BOLD + C_TOOL_Y + opt_disp + RESET + opt_pad + C_BORDER + " │" + RESET)
                    if desc:
                        d_disp = fit_line(desc, max(1, content_w - 4))
                        d_pad = " " * max(0, content_w - 4 - vis_len(d_disp))
                        drop_lines.append(C_BORDER + "│     " + RESET + C_USER + d_disp + RESET + d_pad + C_BORDER + " │" + RESET)
                else:
                    opt_str = f"  [{i + 1}] {lbl}"
                    opt_disp = fit_line(opt_str, content_w)
                    opt_pad = " " * max(0, content_w - vis_len(opt_disp))
                    drop_lines.append(C_BORDER + "│ " + RESET + C_DIM + opt_disp + RESET + opt_pad + C_BORDER + " │" + RESET)
                    if desc:
                        d_disp = fit_line(desc, max(1, content_w - 4))
                        d_pad = " " * max(0, content_w - 4 - vis_len(d_disp))
                        drop_lines.append(C_BORDER + "│     " + RESET + C_DIM + d_disp + RESET + d_pad + C_BORDER + " │" + RESET)

            foot_text = "└─ [↑/↓] iterate • [Enter] confirm • [s] skip • [Esc] cancel "
            drop_lines.append(C_BORDER + foot_text + "─" * max(0, box_w - vis_len(foot_text) - 1) + "┘" + RESET)

            box_h = len(drop_lines)
            start_row = max(1, status_row - box_h)
            start_col = 2
            for idx, d_line in enumerate(drop_lines):
                if start_row + idx < status_row:
                    buf.append(cup(start_row + idx, start_col) + BG_MAIN + d_line)

        elif self.input_text.startswith("/"):
            matched = [c for c in COMMANDS if c[0].startswith(self.input_text.lower())]
            if matched:
                box_w = min(max_line_w - 4, 76)
                content_w = max(10, box_w - 4)
                drop_lines = []
                title_text = "┌─ Commands "
                drop_lines.append(C_BORDER + title_text + "─" * max(0, box_w - vis_len(title_text) - 1) + "┐" + RESET)

                sel = getattr(self, "suggestion_index", 0) % len(matched)
                for i, (cmd_name, cmd_desc) in enumerate(matched[:8]):
                    is_sel = (i == sel)
                    if is_sel:
                        cmd_str = f"❯ {cmd_name:<9} {cmd_desc}"
                        c_disp = fit_line(cmd_str, content_w)
                        c_pad = " " * max(0, content_w - vis_len(c_disp))
                        drop_lines.append(C_BORDER + "│ " + RESET + BOLD + C_CYAN + c_disp + RESET + c_pad + C_BORDER + " │" + RESET)
                    else:
                        cmd_str = f"  {cmd_name:<9} {cmd_desc}"
                        c_disp = fit_line(cmd_str, content_w)
                        c_pad = " " * max(0, content_w - vis_len(c_disp))
                        drop_lines.append(C_BORDER + "│ " + RESET + C_DIM + c_disp + RESET + c_pad + C_BORDER + " │" + RESET)

                foot_text = "└─ [↑/↓] iterate • [Enter] complete "
                drop_lines.append(C_BORDER + foot_text + "─" * max(0, box_w - vis_len(foot_text) - 1) + "┘" + RESET)

                box_h = len(drop_lines)
                start_row = max(1, status_row - box_h)
                start_col = 2
                for idx, d_line in enumerate(drop_lines):
                    if start_row + idx < status_row:
                        buf.append(cup(start_row + idx, start_col) + BG_MAIN + d_line)

        # Position cursor at target column and only then reveal it
        cursor_col = 3 + min(self.input_cursor, max_input)
        buf.append(cup(input_row, cursor_col))
        buf.append(show_cursor())

        self._write("".join(buf))

    # ── Line Cache ────────────────────────────────────────────────

    def _render_tool_node_lines(self, node, w):
        out = []
        tc = TOOL_COLORS.get(node.name.lower(), C_TOOL)
        spinner_f = SPINNER[self.spinner_i % len(SPINNER)]
        clean_detail = format_path(node.detail)
        if clean_detail and clean_detail.startswith("{") and clean_detail.endswith("}"):
            try:
                d = json.loads(clean_detail)
                vals = [format_path(str(v)) for v in d.values() if isinstance(v, (str, int, float))]
                if vals:
                    clean_detail = " ".join(vals)
            except Exception:
                pass
        detail_s = f"({clean_detail})" if clean_detail else ""
        elapsed = node.elapsed_str()

        r = None
        if node.result_text:
            try:
                r = json.loads(node.result_text)
            except Exception:
                pass

        summary = get_tool_summary(node.name, node.detail, node.result_text)

        flat_logs = []
        for stream, log_text in node.logs:
            for line in log_text.split("\n"):
                if line:
                    flat_logs.append((stream, line))

        if not flat_logs and r and isinstance(r, dict) and node.done:
            text_out = r.get("stdout") or r.get("output") or r.get("error")
            if text_out and isinstance(text_out, str):
                for l in text_out.split("\n"):
                    if l:
                        flat_logs.append(("stdout", l))

        is_collapsed = self.compact_mode and not node.expanded
        is_diff_cmd = bool(node and ("diff" in node.name.lower() or "diff" in node.detail.lower()))

        if not node.done:
            icon = f"{C_CYAN}{spinner_f}{RESET}"
            status_s = f" {C_CYAN}[running {elapsed}]{RESET}"
        elif node.error:
            icon = f"{C_ERR}●{RESET}"
            status_s = f" {C_ERR}[failed ✗ {elapsed}]{RESET}"
        else:
            icon = f"{tc}●{RESET}"
            status_s = ""

        header_line = f"  {icon} {BOLD}{tc}{node.name}{RESET}{C_DIM}{detail_s}{RESET}{status_s}"
        out.append(fit_line(header_line, w))

        if not node.done:
            if flat_logs:
                for l_idx, (stream, log_text) in enumerate(flat_logs[-4:]):
                    prefix = f"  {C_DIM}⎿  {RESET}" if l_idx == 0 else "     "
                    c_line = format_log_line(stream, log_text, is_diff=is_diff_cmd)
                    out.append(fit_line(f"{prefix}{c_line}", w))
            else:
                out.append(f"  {C_DIM}⎿  {spinner_f} running…{RESET}")

        elif is_collapsed:
            if summary:
                hint = f" {C_DIM}(ctrl+o to expand){RESET}" if (node.name.lower() in ("edit", "read", "bash") and (flat_logs or (r and "diff" in r))) else ""
                out.append(fit_line(f"  {C_DIM}⎿  {RESET}{C_DIM}{summary}{RESET}{hint}", w))
            elif flat_logs:
                max_preview = 6
                if len(flat_logs) > max_preview:
                    omitted = len(flat_logs) - max_preview
                    preview_logs = flat_logs[-max_preview:]
                    branch_hdr = f"  {C_DIM}⎿  <output +{omitted} lines>{RESET}"
                    out.append(fit_line(branch_hdr, w))
                    for l_idx, (stream, log_text) in enumerate(preview_logs):
                        is_last = (l_idx == len(preview_logs) - 1)
                        hint = f" {C_DIM}(ctrl+o to collapse){RESET}" if is_last else ""
                        c_line = format_log_line(stream, log_text, is_diff=is_diff_cmd)
                        out.append(fit_line(f"     {c_line}{hint}", w))
                else:
                    for l_idx, (stream, log_text) in enumerate(flat_logs):
                        is_first = (l_idx == 0)
                        prefix = f"  {C_DIM}⎿  {RESET}" if is_first else "     "
                        c_line = format_log_line(stream, log_text, is_diff=is_diff_cmd)
                        out.append(fit_line(f"{prefix}{c_line}", w))
            else:
                out.append(f"  {C_DIM}⎿  done.{RESET}")

        else:
            # Expanded mode (Ctrl+O)
            if node.name.lower() == "edit" and r and "diff" in r:
                diff_lines = colorize_diff(r["diff"])
                out.append(fit_line(f"  {C_DIM}⎿  {RESET}{C_DIM}{summary or 'Diff'}{RESET} {C_DIM}(ctrl+o to collapse){RESET}", w))
                for dl in diff_lines[:40]:
                    out.append(fit_line(f"     {dl}", w))
                if len(diff_lines) > 40:
                    out.append(fit_line(f"     {C_DIM}… [{len(diff_lines)-40} more diff lines]{RESET}", w))
            elif node.name.lower() == "read" and r and "content" in r:
                out.append(fit_line(f"  {C_DIM}⎿  {RESET}{C_DIM}{summary or 'Content'}{RESET} {C_DIM}(ctrl+o to collapse){RESET}", w))
                c_lines = r["content"].splitlines()
                for cl in c_lines[:25]:
                    out.append(fit_line(f"     {C_DIM}{cl}{RESET}", w))
                if len(c_lines) > 25:
                    out.append(fit_line(f"     {C_DIM}… [{len(c_lines)-25} more lines hidden]{RESET}", w))
            elif flat_logs:
                for l_idx, (stream, log_text) in enumerate(flat_logs):
                    prefix = f"  {C_DIM}⎿  {RESET}" if l_idx == 0 else "     "
                    is_last = (l_idx == len(flat_logs) - 1)
                    hint = f" {C_DIM}(ctrl+o to collapse){RESET}" if is_last else ""
                    c_line = format_log_line(stream, log_text, is_diff=is_diff_cmd)
                    out.append(fit_line(f"{prefix}{c_line}{hint}", w))
            elif summary:
                out.append(fit_line(f"  {C_DIM}⎿  {RESET}{C_DIM}{summary}{RESET}", w))

        out.append("")
        return out

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

            # Chronological Blocks (thinking -> tool -> thinking -> tool -> ai)
            if turn.blocks:
                for block in turn.blocks:
                    b_type = block.get("type")
                    if b_type == "thinking":
                        chunks = block.get("chunks", [])
                        thought_text = "".join(chunks).strip()
                        if thought_text:
                            is_active = (turn is self.cur_turn and not block.get("done") and self.thinking)
                            dur = (time.time() - block["start_time"]) if is_active else (
                                (block["end_time"] - block["start_time"]) if block.get("end_time") else 0.0
                            )
                            spinner_f = SPINNER[self.spinner_i % len(SPINNER)]
                            if is_active:
                                header = f"  {C_CYAN}{spinner_f} Thinking ({dur:.1f}s){RESET}"
                            else:
                                dur_s = f" ({dur:.1f}s)" if dur >= 0.5 else ""
                                header = f"  {C_DIM}💭 Thought process{dur_s}{RESET}"
                            lines.append(header)
                            for tl in wrap_text(thought_text, w - 6, indent=0):
                                lines.append(f"     {C_DIM}{ITALIC}{tl}{RESET}")
                            lines.append("")

                    elif b_type == "tool":
                        tid = block.get("id")
                        node = turn.tool_nodes.get(tid)
                        if node:
                            lines.extend(self._render_tool_node_lines(node, w))

                    elif b_type == "ai":
                        ai_lines = block.get("lines", [])
                        if ai_lines:
                            if turn is not self.cur_turn:
                                full_ai_text = "\n".join(ai_lines)
                                for ll in render_markdown_ansi(full_ai_text, w - 2, indent=2):
                                    lines.append(ll)
                            else:
                                for al in ai_lines:
                                    for ll in wrap_text(al, w - 4, indent=0):
                                        lines.append("  " + C_AI + ll + RESET)
                            lines.append("")
            else:
                # Fallback if no blocks recorded
                if turn.thinking_chunks:
                    full_thought = "".join(turn.thinking_chunks).strip()
                    if full_thought:
                        lines.append(f"  {C_DIM}💭 Thought process{RESET}")
                        for tl in wrap_text(full_thought, w - 6, indent=0):
                            lines.append(f"     {C_DIM}{ITALIC}{tl}{RESET}")
                        lines.append("")

                for idx, tid in enumerate(turn.tool_order):
                    node = turn.tool_nodes.get(tid)
                    if node:
                        lines.extend(self._render_tool_node_lines(node, w))

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

            if getattr(turn, "interrupted", False):
                lines.append(f"  {C_DIM}interrupted{RESET}")
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

        # Compact / normalize blank lines: never allow consecutive empty lines, and strip trailing empty lines
        compact_lines = []
        for line in lines:
            if not line.strip():
                if compact_lines and not compact_lines[-1].strip():
                    continue
                compact_lines.append("")
            else:
                compact_lines.append(line)
        while compact_lines and not compact_lines[-1].strip():
            compact_lines.pop()

        self._lines_cache = compact_lines
        self._cache_dirty = False
        return compact_lines

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
            if (self.cur_turn.blocks 
                    and self.cur_turn.blocks[-1].get("type") == "thinking" 
                    and not self.cur_turn.blocks[-1].get("done")):
                self.cur_turn.blocks[-1]["done"] = True
                self.cur_turn.blocks[-1]["end_time"] = time.time()
            self.cur_turn.blocks.append({"type": "tool", "id": tool_id})
            node = ToolNode(tool_id, name, detail)
            node.expanded = not self.compact_mode
            self.cur_turn.tool_nodes[tool_id] = node
            self.cur_turn.tool_order.append(tool_id)
            self.thinking = False
            self.active_thinking = ""
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
            if (self.cur_turn.blocks 
                    and self.cur_turn.blocks[-1].get("type") == "thinking" 
                    and not self.cur_turn.blocks[-1].get("done")):
                self.cur_turn.blocks[-1]["done"] = True
                self.cur_turn.blocks[-1]["end_time"] = time.time()
            if self.cur_turn.blocks and self.cur_turn.blocks[-1].get("type") == "ai":
                self.cur_turn.blocks[-1]["lines"].append(text)
            else:
                self.cur_turn.blocks.append({"type": "ai", "lines": [text]})
            self.cur_turn.ai_lines.append(text)
            self._cache_dirty = True

    def end_turn(self):
        with self._lock:
            if self.cur_turn and self.cur_turn.blocks:
                for b in self.cur_turn.blocks:
                    if b.get("type") == "thinking" and not b.get("done"):
                        b["done"] = True
                        if not b.get("end_time"):
                            b["end_time"] = time.time()
            self.cur_turn = None
            self._question_state = None
            self._edit_state = None
            self.busy = False
            self.thinking = False
            self.active_tool_name = ""
            self.active_tool_detail = ""
            self.active_thinking = ""
            self._cache_dirty = True

    def mark_interrupted(self):
        with self._lock:
            if self.cur_turn and not getattr(self.cur_turn, "interrupted", False):
                self.cur_turn.interrupted = True
                if self.cur_turn.blocks:
                    for b in self.cur_turn.blocks:
                        if not b.get("done"):
                            b["done"] = True
                            if not b.get("end_time"):
                                b["end_time"] = time.time()
            self.active_tool_name = ""
            self.active_tool_detail = ""
            self.cur_turn = None
            self._question_state = None
            self._edit_state = None
            self.busy = False
            self.thinking = False
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

        # Bracketed paste: \x1b[200~ ... \x1b[201~
        if seq.startswith(b"\x1b[200~"):
            paste_bytes = seq[6:]
            while b"\x1b[201~" not in paste_bytes:
                r, _, _ = select.select([fd], [], [], 0.05)
                if not r:
                    break
                try:
                    c = os.read(fd, 4096)
                    if not c:
                        break
                    paste_bytes += c
                except Exception:
                    break
            if b"\x1b[201~" in paste_bytes:
                paste_bytes = paste_bytes[:paste_bytes.index(b"\x1b[201~")]
            pasted_text = paste_bytes.decode("utf-8", errors="replace")
            return ("PASTE", pasted_text)

        # SGR mouse event: \x1b[<cb;cx;cy(M|m)
        if seq.startswith(b"\x1b[<"):
            m = re.match(rb"^\x1b\[<(\d+);(\d+);(\d+)([Mm])", seq)
            if m:
                cb = int(m.group(1))
                if cb & 64:
                    if (cb & 3) == 0:
                        return "WHEEL_UP"
                    elif (cb & 3) == 1:
                        return "WHEEL_DOWN"
                elif cb == 64:
                    return "WHEEL_UP"
                elif cb == 65:
                    return "WHEEL_DOWN"
                return "MOUSE_EVENT"

        # Standard X10/X11 mouse event: \x1b[M cb cx cy
        if seq.startswith(b"\x1b[M") and len(seq) >= 6:
            cb = seq[3]
            cb_val = cb - 32 if cb >= 32 else cb
            if cb_val & 64:
                if (cb_val & 3) == 0:
                    return "WHEEL_UP"
                elif (cb_val & 3) == 1:
                    return "WHEEL_DOWN"
            elif cb in (96, 64):
                return "WHEEL_UP"
            elif cb in (97, 65):
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
        self.suggestion_index = 0

        # Model configuration
        self.active_model = "mistral-medium-latest"
        self.model_effort = "medium"
        self.model_badge = "mistral-medium"

        # Database & Environment
        self.project_dir = os.path.abspath(os.getcwd())
        load_project_env(self.project_dir)
        self.chat_db = ChatDB()
        self.current_chat_id = self.chat_db.create_chat(self.project_dir, model=self.active_model)
        self._pending_key_entry = None

    def start(self):
        self._start_worker()
        threading.Thread(target=self._spinner_loop, daemon=True).start()
        threading.Thread(target=self._protocol_loop, daemon=True).start()
        try:
            signal.signal(signal.SIGWINCH, lambda s, f: self.screen.redraw())
        except Exception:
            pass
        self.screen.set_status("", C_STATUS)

        env_status = get_api_keys_status(self.project_dir)
        if not env_status["has_any_key"]:
            self._handle_cmd_onboarding(is_startup=True)
        else:
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
                    self.screen.finish_tool_node(tid, result_text)
                    self.screen.finish_active_tool()
                    self.screen.redraw()
                except Exception:
                    pass
                continue

            if line.startswith("<<ASK_USER_QUESTION>>"):
                try:
                    p = json.loads(line[len("<<ASK_USER_QUESTION>>"):])
                    for q in p.get("questions", []):
                        opts = q.get("options", [])
                        has_other = any(
                            o.get("label", "").lower() in ("other", "custom", "other…", "custom answer")
                            for o in opts
                        )
                        if not has_other:
                            opts.append({
                                "label": "Other",
                                "description": "Type your own custom answer",
                                "custom": True,
                            })
                        has_skip = any(
                            o.get("label", "").lower() in ("skip", "skip (continue)") or o.get("skip")
                            for o in opts
                        )
                        if not has_skip:
                            opts.append({
                                "label": "Skip",
                                "description": "Skip (continue without answering)",
                                "skip": True,
                            })
                    self._question_state = {
                        "id": p["id"],
                        "questions": p["questions"],
                        "answers": [],
                        "index": 0,
                        "selected_option": 0,
                    }
                    self.screen.set_question(self._question_state)
                    self.screen.set_status("? Waiting for your answer (press 's' to skip)", C_TOOL_Y)
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
                        self.screen.redraw()
                except Exception:
                    pass
                continue

            if line.startswith("<<AGENTJOB_RESULT>>"):
                continue

            if line == "<<END>>":
                self._busy = False
                self._tool_active = False
                self._interrupted = False
                self.screen.finish_active_tool()
                self.screen.set_busy(False)
                if self.screen.cur_turn and not self.screen.cur_turn.is_system:
                    ai_text = "\n".join(self.screen.cur_turn.ai_lines).strip()
                    if ai_text and getattr(self, "current_chat_id", None):
                        try:
                            self.chat_db.add_message(self.current_chat_id, "assistant", ai_text)
                        except Exception:
                            pass
                self.screen.end_turn()
                self.screen.set_status("", C_READY)
                self.screen.redraw()
                continue

            # Streamed text
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
            if self._busy:
                self._do_interrupt()
                return
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
                    chosen = matched[self.suggestion_index % len(matched)]
                    with s._lock:
                        s.input_text = chosen
                        s.input_cursor = len(s.input_text)
                        s._cache_dirty = True
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

        if isinstance(key, tuple) and key[0] == "PASTE":
            pasted = key[1].replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
            if pasted:
                with s._lock:
                    s.input_text = (s.input_text[:s.input_cursor]
                                    + pasted
                                    + s.input_text[s.input_cursor:])
                    s.input_cursor += len(pasted)
                    s._cache_dirty = True
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

            if s.input_text.startswith("/"):
                matched = [c for c in COMMANDS if c[0].startswith(s.input_text.lower())]
                if matched:
                    self.suggestion_index = (self.suggestion_index - 1) % len(matched)
                    with s._lock:
                        s.suggestion_index = self.suggestion_index
                        s._cache_dirty = True
                    s.redraw()
                    return

            # If user has scrolled up, UP arrow scrolls conversation further up
            if s.scroll_offset > 0:
                s.scroll_up(3)
                s.redraw()
                return

            # If busy generating, UP arrow scrolls conversation
            if self._busy or s.busy:
                s.scroll_up(3)
                s.redraw()
                return

            if self.history:
                if self.history_index == -1:
                    self.saved_input = s.input_text
                    self.history_index = len(self.history) - 1
                elif self.history_index > 0:
                    self.history_index -= 1
                elif self.history_index == 0:
                    lines = s._get_lines()
                    if len(lines) > s._convo_h:
                        s.scroll_up(3)
                        s.redraw()
                        return

                if 0 <= self.history_index < len(self.history):
                    with s._lock:
                        s.input_text = self.history[self.history_index]
                        s.input_cursor = len(s.input_text)
                        s.history_badge = f"history {self.history_index + 1}/{len(self.history)}"
                        s._cache_dirty = True
                    s.redraw()
                    return

            lines = s._get_lines()
            if len(lines) > s._convo_h:
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

            if s.input_text.startswith("/"):
                matched = [c for c in COMMANDS if c[0].startswith(s.input_text.lower())]
                if matched:
                    self.suggestion_index = (self.suggestion_index + 1) % len(matched)
                    with s._lock:
                        s.suggestion_index = self.suggestion_index
                        s._cache_dirty = True
                    s.redraw()
                    return

            # If user has scrolled up, DOWN arrow scrolls down towards latest lines
            if s.scroll_offset > 0:
                s.scroll_down(3)
                s.redraw()
                return

            # If busy generating, DOWN arrow scrolls conversation
            if self._busy or s.busy:
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
                        s._cache_dirty = True
                else:
                    with s._lock:
                        s.input_text = self.history[self.history_index]
                        s.input_cursor = len(s.input_text)
                        s.history_badge = f"history {self.history_index + 1}/{len(self.history)}"
                        s._cache_dirty = True
                s.redraw()
                return

        if key == "ESC":
            if self._edit_state:
                self._submit_edit_approval(False)
                return
            if self._question_state:
                self._cancel_question()
                return
            if s.input_text.startswith("/"):
                s.input_clear()
                self.history_index = -1
                self.suggestion_index = 0
                with s._lock:
                    s.suggestion_index = 0
                    s._cache_dirty = True
                s.redraw()
                return
            if self._busy:
                self._do_interrupt()
                return
            if s.scroll_offset > 0:
                s.scroll_offset = 0
                s.redraw()
                return
            if s.input_text:
                s.input_clear()
                self.history_index = -1
                s.redraw()
            return

        if key == "CTRL_U":
            s.input_clear()
            self.history_index = -1
            self.suggestion_index = 0
            with s._lock:
                s.suggestion_index = 0
                s._cache_dirty = True
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
            self.suggestion_index = 0
            with s._lock:
                s.suggestion_index = 0
                s._cache_dirty = True
            s.redraw()
            return

        if key == "ENTER":
            if self._question_state:
                self._do_submit()
                return

            if s.input_text.startswith("/"):
                matched = [c for c in COMMANDS if c[0].startswith(s.input_text.lower())]
                if matched:
                    chosen = matched[self.suggestion_index % len(matched)][0]
                    s.input_clear()
                    self.history_index = -1
                    self.suggestion_index = 0
                    with s._lock:
                        s.suggestion_index = 0
                        s._cache_dirty = True
                    self._execute_slash_command(chosen)
                    return

            self._do_submit()
            return

        if self._question_state and key in "123456789" and not s.input_text:
            idx = int(key) - 1
            qs = self._question_state
            q = qs["questions"][qs["index"]]
            opts = q.get("options", [])
            if 0 <= idx < len(opts):
                qs["selected_option"] = idx
                if opts[idx].get("custom"):
                    with s._lock:
                        s._cache_dirty = True
                    s.set_status("Type your custom answer and press Enter", C_TOOL_Y)
                    s.redraw()
                    return
                self._submit_question_choice()
                return

        if self._question_state and not s.input_text and key.lower() in ("o", "c"):
            qs = self._question_state
            q = qs["questions"][qs["index"]]
            opts = q.get("options", [])
            for idx, opt in enumerate(opts):
                if opt.get("custom") or opt.get("label", "").lower() in ("other", "custom"):
                    with s._lock:
                        qs["selected_option"] = idx
                        s._cache_dirty = True
                    s.set_status("Type your custom answer and press Enter", C_TOOL_Y)
                    s.redraw()
                    return

        if self._question_state and not s.input_text and key.lower() == "s":
            self._skip_question()
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
            self.suggestion_index = 0
            with s._lock:
                s.suggestion_index = 0
                s._cache_dirty = True
            s.redraw()
            return

    def _execute_slash_command(self, text):
        cmd = text.split()[0].lower()
        if cmd == "/help":
            self._handle_cmd_help()
            return True
        elif cmd == "/clear":
            self._handle_cmd_clear()
            return True
        elif cmd == "/status":
            self._handle_cmd_status()
            return True
        elif cmd == "/diff":
            self._handle_cmd_diff()
            return True
        elif cmd == "/tools":
            self._handle_cmd_tools()
            return True
        elif cmd == "/models":
            self._handle_cmd_models()
            return True
        elif cmd == "/model":
            self._handle_cmd_model()
            return True
        elif cmd == "/delete":
            self._handle_cmd_delete()
            return True
        elif cmd == "/onboarding":
            self._handle_cmd_onboarding()
            return True
        elif cmd == "/history":
            self._handle_cmd_history()
            return True
        elif cmd == "/compact":
            self.screen.toggle_tool_expand()
            mode = "compact" if self.screen.compact_mode else "expanded"
            self.screen.set_status(f"tool logs set to {mode}", C_OK)
            self.screen.redraw()
            return True
        elif cmd in ("/exit", "/quit", "q"):
            self._running = False
            return True
        return False

    def _do_submit(self):
        if getattr(self, "_pending_key_entry", None) is not None:
            key_name = self._pending_key_entry
            self._pending_key_entry = None
            val = self.screen.take_input().strip()
            if not val:
                self.screen.set_status("Key entry cancelled", C_DIM)
                self.screen.redraw()
                return
            env_file = save_api_key(self.project_dir, key_name, val)
            self._send_line("<<SET_KEYS>>" + json.dumps({key_name: val}))
            turn = self.screen.new_turn(f"/onboarding ({key_name})", is_system=True)
            turn.ai_lines.append(f"✓ Saved **{key_name}** to `{env_file}` and updated active session!")
            self.screen.end_turn()
            self.screen.set_status(f"✓ {key_name} saved", C_OK)
            self.screen.redraw()
            return

        if self._question_state is not None:
            text = self.screen.take_input().strip()
            qs = self._question_state
            q = qs["questions"][qs["index"]]
            opts = q.get("options", [])
            sel_idx = qs.get("selected_option", 0)
            is_custom = (sel_idx < len(opts) and opts[sel_idx].get("custom"))
            if text:
                ans = text
            elif is_custom:
                self.screen.set_status("Please type your custom answer and press Enter", C_WARN)
                self.screen.redraw()
                return
            else:
                if 0 <= sel_idx < len(opts):
                    opt = opts[sel_idx]
                    if opt.get("skip") or opt.get("label", "").lower() == "skip":
                        self._skip_question()
                        return
                    ans = opt["label"]
                else:
                    ans = ""
            self._submit_question_answer(ans)
            return

        if self._edit_state is not None:
            text = self.screen.take_input().strip()
            if not text:
                self._submit_edit_approval(True)
            else:
                approved = text.casefold() in ("y", "yes", "approve", "approved")
                self._submit_edit_approval(approved)
            return

        if self._busy:
            self.screen.set_status("generating… press Esc to interrupt first", C_WARN)
            self.screen.redraw()
            return

        text = self.screen.take_input().strip()
        if not text:
            return

        if not self.history or self.history[-1] != text:
            self.history.append(text)
        self.history_index = -1
        self.saved_input = ""

        # Slash Commands
        if text.startswith("/"):
            if self._execute_slash_command(text):
                return

        # Normal prompt to worker
        if getattr(self, "current_chat_id", None):
            try:
                self.chat_db.add_message(self.current_chat_id, "user", text)
            except Exception:
                pass
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
| `/models` | Switch model (Mistral / Gemini 3.8 Flash) |
| `/delete` | Pick and delete a chat from project history |
| `/onboarding` | Configure or switch API keys (Mistral / Gemini) |
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
| `Write` | Create or overwrite files directly with full content |
| `Edit` | Surgical search-and-replace with diff approval |
| `Glob` | File pattern search |
| `Grep` | Fast regex content search |
| `WebSearch` | Live DuckDuckGo internet web search |
| `WebFetch` | Fetch and extract text content from web URLs |
| `TaskCreate` | Create structured session tasks and sub-goals |
| `TaskList` | List session tasks and status |
| `TaskUpdate` | Update status or details of session tasks |
| `TaskGet` | Get detailed information for a specific task |
| `TaskStop` | Cancel or stop an active task |
| `LSP` | Definitions, diagnostics & hover from language server |
| `ListMcpResourcesTool` | Discover configured MCP server resources |
| `ReadMcpResourceTool` | Read resource contents from MCP servers |
| `artifact` | Interactive HTML preview server |
| `agentjob` | Background front-end implementation runner |
| `askUserQuestion`| Interactive multiple-choice prompts |
| `EnterPlanMode` | Read-only planning mode |
| `ExitPlanMode` | Resume change execution |
| `CronCreate` | Schedule session prompts |
| `CronList` | List scheduled cron jobs |
| `Monitor` | Inspect background processes, logs & PID status |
| `PushNotification`| Send desktop or terminal alert notification |
| `RemoteTrigger` | Trigger HTTP webhooks or remote endpoints |
| `ReportFindings` | Save structured research and audit reports |
| `ScheduleWakeup` | One-shot delayed timer / wakeup alert |
| `SendMessage` | Steer agentjob or broadcast message |
| `SendUserFile` | Export and stage workspace file for user |
| `ShareOnboardingGuide`| Generate repository ONBOARDING.md guide |
| `Skill` | Execute or list project automation skills |
| `TaskOutput` | Attach outputs and artifacts to tasks |
| `TodoWrite` | Manage markdown task checklist (TODO.md) |
| `ToolSearch` | Discover and search available tools |
| `WaitForMcpServers`| Wait for MCP server initializations |
| `Workflow` | Execute multi-step sequential tool workflows |
"""
        turn.ai_lines.append(tools_md.strip())
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_model(self):
        turn = self.screen.new_turn("/model", is_system=True)
        effort_s = f"\n- **Thinking Effort**: `{self.model_effort}`" if self.active_model.startswith("gemini") else ""
        model_md = f"""
### Model Info

- **Active Model**: `{self.active_model}`{effort_s}
- **Status Badge**: `{self.model_badge}`
- **Worker**: `live_cli.py` (asyncio event loop)
- **Protocol**: Framing `<<TOOL_*>>`, `<<ASK_*>>`, `<<EDIT_*>>`, `<<SET_MODEL>>`

Type `/models` to switch between `mistral-medium-latest` and `Gemini 3.8 Flash`.
"""
        turn.ai_lines.append(model_md.strip())
        self.screen.end_turn()
        self.screen.redraw()

    def _handle_cmd_models(self):
        cur_sel = 0
        if self.active_model == "mistral-medium-latest":
            cur_sel = 0
        elif self.active_model == "gemini-3.7-flash":
            effort = getattr(self, "model_effort", "medium")
            if effort == "low":
                cur_sel = 2
            elif effort == "high":
                cur_sel = 3
            else:
                cur_sel = 1
        elif self.active_model == "gemini-3.5-flash":
            cur_sel = 4
        elif self.active_model == "gemini-3.5-flash-lite":
            cur_sel = 5
        elif self.active_model == "gemini-3.6-flash":
            cur_sel = 6
        elif self.active_model == "gemini-3.1-flash-lite":
            cur_sel = 7
        elif self.active_model == "gemini-3.8-flash":
            cur_sel = 8

        qs = {
            "id": "model_select_1",
            "header": "Select Active Model & Config",
            "questions": [{
                "header": "Active Model",
                "question": "Choose AI model and reasoning effort:",
                "options": [
                    {"label": "mistral-medium-latest", "description": "Mistral Large reasoning & tool execution (fast, no extra config)"},
                    {"label": "Gemini 3.7 Flash (medium - Recommended)", "description": "Google GenAI 3.7 Flash — 4,096 tokens thinking budget (free tier active)"},
                    {"label": "Gemini 3.7 Flash (low)", "description": "Google GenAI 3.7 Flash — 1,024 tokens thinking budget (free tier active)"},
                    {"label": "Gemini 3.7 Flash (high)", "description": "Google GenAI 3.7 Flash — 16,384 tokens thinking budget (free tier active)"},
                    {"label": "Gemini 3.5 Flash", "description": "Google GenAI 3.5 Flash — Fast tool-calling model (free tier active)"},
                    {"label": "Gemini 3.5 Flash Lite", "description": "Google GenAI 3.5 Flash Lite — Lightweight, minimal latency (free tier active)"},
                    {"label": "Gemini 3.6 Flash", "description": "Google GenAI 3.6 Flash — Balanced capability (free tier active)"},
                    {"label": "Gemini 3.1 Flash Lite", "description": "Google GenAI 3.1 Flash Lite — Ultra-fast responses (free tier active)"},
                    {"label": "Gemini 3.8 Flash (medium)", "description": "Google GenAI 3.8 Flash — 4,096 tokens thinking budget (20 req/day limit)"},
                    {"label": "Skip", "description": "Keep current model unchanged", "skip": True},
                ]
            }],
            "index": 0,
            "answers": [],
            "selected_option": cur_sel,
            "is_model_picker": True,
            "step": 1,
        }
        self._question_state = qs
        self.screen.set_question(qs)
        self.screen.set_status("Select model: ↑/↓ choose • Enter confirm • 's' or Esc cancel", C_TOOL_Y)
        self.screen.redraw()

    def _handle_cmd_delete(self):
        chats = self.chat_db.get_chats(self.project_dir)
        if not chats:
            turn = self.screen.new_turn("/delete", is_system=True)
            turn.ai_lines.append(f"> [!NOTE]\n> No saved chats found for project `{self.project_dir}`.")
            self.screen.end_turn()
            self.screen.redraw()
            return

        options = []
        for c in chats:
            c_time = datetime.fromtimestamp(c.get("created_at", time.time())).strftime("%b %d, %H:%M")
            cnt = c.get("message_count", 0)
            is_active = (c["id"] == self.current_chat_id)
            tag = " (Active)" if is_active else ""
            title = c.get("title", "Untitled Chat")
            label = f"{title[:40]}{tag}"
            desc = f"{c_time} • {cnt} msgs • {c.get('model', 'mistral')} • ID: {c['id'][-8:]}"
            options.append({
                "label": label,
                "description": desc,
                "chat_id": c["id"],
                "chat_title": title,
            })
        options.append({
            "label": "Cancel",
            "description": "Keep all chats and exit menu",
            "cancel": True,
            "skip": True,
        })

        qs = {
            "id": "chat_delete_picker",
            "header": "Delete Stored Chat",
            "questions": [{
                "header": "Select Chat to Delete",
                "question": f"Choose a chat from '{os.path.basename(self.project_dir)}' to permanently delete:",
                "options": options,
            }],
            "index": 0,
            "answers": [],
            "selected_option": 0,
            "is_delete_picker": True,
        }
        self._question_state = qs
        self.screen.set_question(qs)
        self.screen.set_status("Select chat to delete: ↑/↓ choose • Enter confirm • Esc cancel", C_TOOL_Y)
        self.screen.redraw()

    def _handle_cmd_onboarding(self, is_startup=False):
        status = get_api_keys_status(self.project_dir)
        instr = get_onboarding_instructions(status)
        turn = self.screen.new_turn("Welcome to Priya" if is_startup else "/onboarding", is_system=True)
        turn.ai_lines.append(instr.strip())
        self.screen.end_turn()

        options = [
            {
                "label": "Configure Mistral API Key",
                "description": f"Enter or replace MISTRAL_API_KEY (Currently: {status['mistral_masked']})",
                "key_target": "MISTRAL_API_KEY",
            },
            {
                "label": "Configure Google Gemini API Key",
                "description": f"Enter or replace GEMINI_API_KEY (Currently: {status['gemini_masked']})",
                "key_target": "GEMINI_API_KEY",
            },
            {
                "label": "Done / Continue",
                "description": "Proceed to Priya chat prompt",
                "cancel": True,
                "skip": True,
            }
        ]

        qs = {
            "id": "onboarding_picker",
            "header": "Priya API Key Setup",
            "questions": [{
                "header": "Configure API Key",
                "question": "Select an API key to configure or update for this project:",
                "options": options,
            }],
            "index": 0,
            "answers": [],
            "selected_option": 0,
            "is_onboarding_picker": True,
        }
        self._question_state = qs
        self.screen.set_question(qs)
        self.screen.set_status("Select key to configure: ↑/↓ choose • Enter confirm • Esc dismiss", C_TOOL_Y)
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
        if 0 <= sel_idx < len(opts):
            opt = opts[sel_idx]
            if opt.get("skip") or opt.get("label", "").lower() == "skip":
                self._skip_question()
                return
            if opt.get("custom"):
                text = self.screen.take_input().strip()
                if not text:
                    self.screen.set_status("Please type your custom answer and press Enter", C_WARN)
                    self.screen.redraw()
                    return
                self._submit_question_answer(text)
                return
            ans = opt["label"]
            self._submit_question_answer(ans)

    def _submit_question_answer(self, ans):
        qs = self._question_state
        if not qs:
            return

        if qs.get("is_model_picker"):
            ans_clean = ans.strip().lower()
            if ans_clean == "skip":
                self._skip_question()
                return

            if "mistral" in ans_clean:
                self.active_model = "mistral-medium-latest"
                self.model_effort = "none"
                self.model_badge = "mistral-medium"
                self.screen.set_model_badge(self.model_badge)
                self._question_state = None
                self.screen.set_question(None)
                self.screen.set_status(f"✓ Active model: {self.active_model}", C_CYAN)
                self._send_line("<<SET_MODEL>>" + json.dumps({"model": "mistral-medium-latest"}))
                turn = self.screen.new_turn("/models", is_system=True)
                turn.ai_lines.append("✓ Switched active model to **mistral-medium-latest**")
                self.screen.end_turn()
                self.screen.redraw()
                return

            if "3.8" in ans_clean:
                model = "gemini-3.8-flash"
                if "low" in ans_clean:
                    effort, budget = "low", 1024
                elif "high" in ans_clean:
                    effort, budget = "high", 16384
                else:
                    effort, budget = "medium", 4096
                badge = f"gemini-3.8-flash ({effort})"
            elif "3.7" in ans_clean:
                model = "gemini-3.7-flash"
                if "low" in ans_clean:
                    effort, budget = "low", 1024
                elif "high" in ans_clean:
                    effort, budget = "high", 16384
                else:
                    effort, budget = "medium", 4096
                badge = f"gemini-3.7-flash ({effort})"
            elif "3.5" in ans_clean:
                if "lite" in ans_clean:
                    model, effort, budget, badge = "gemini-3.5-flash-lite", "none", 0, "gemini-3.5-lite"
                else:
                    model, effort, budget, badge = "gemini-3.5-flash", "none", 0, "gemini-3.5-flash"
            elif "3.6" in ans_clean:
                model, effort, budget, badge = "gemini-3.6-flash", "none", 0, "gemini-3.6-flash"
            elif "3.1" in ans_clean:
                model, effort, budget, badge = "gemini-3.1-flash-lite", "none", 0, "gemini-3.1-lite"
            elif ans_clean in ("low", "medium", "high"):
                model = "gemini-3.7-flash"
                effort = ans_clean
                budget_map = {"low": 1024, "medium": 4096, "high": 16384}
                budget = budget_map[effort]
                badge = f"gemini-3.7-flash ({effort})"
            else:
                model, effort, budget, badge = "gemini-3.7-flash", "medium", 4096, "gemini-3.7-flash (medium)"

            self.active_model = model
            self.model_effort = effort
            self.model_badge = badge
            self.screen.set_model_badge(self.model_badge)
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status(f"✓ Active model: {badge}", C_CYAN)
            self._send_line("<<SET_MODEL>>" + json.dumps({
                "model": model,
                "effort": effort,
                "budget": budget
            }))
            if getattr(self, "current_chat_id", None):
                try:
                    self.chat_db.update_chat_model(self.current_chat_id, model)
                except Exception:
                    pass
            turn = self.screen.new_turn("/models", is_system=True)
            turn.ai_lines.append(f"✓ Switched active model to **{model}**" + (f" (effort: `{effort}`)" if effort != "none" else ""))
            self.screen.end_turn()
            self.screen.redraw()
            return

        if qs.get("is_delete_picker"):
            q = qs["questions"][qs["index"]]
            opts = q.get("options", [])
            sel_idx = qs.get("selected_option", 0)
            opt = opts[sel_idx] if 0 <= sel_idx < len(opts) else {}
            if opt.get("cancel") or opt.get("skip") or ans.strip().lower() in ("cancel", "skip"):
                self._question_state = None
                self.screen.set_question(None)
                self.screen.set_status("⊘ Chat deletion cancelled", C_DIM)
                self.screen.redraw()
                return
            chat_id = opt.get("chat_id")
            chat_title = opt.get("chat_title", opt.get("label", "Chat"))
            self._question_state = None
            self.screen.set_question(None)
            if chat_id:
                deleted = self.chat_db.delete_chat(chat_id)
                turn = self.screen.new_turn("/delete", is_system=True)
                if deleted:
                    turn.ai_lines.append(f"✓ Permanently deleted chat: **{chat_title}** (`{chat_id}`)")
                    if chat_id == self.current_chat_id:
                        self.current_chat_id = self.chat_db.create_chat(self.project_dir, model=self.active_model)
                        turn.ai_lines.append("✓ Started new empty chat session.")
                    self.screen.set_status("✓ Chat deleted", C_OK)
                else:
                    turn.ai_lines.append(f"✗ Failed to delete chat `{chat_id}` (not found).")
                    self.screen.set_status("Chat delete failed", C_ERR)
                self.screen.end_turn()
            self.screen.redraw()
            return

        if qs.get("is_onboarding_picker"):
            q = qs["questions"][qs["index"]]
            opts = q.get("options", [])
            sel_idx = qs.get("selected_option", 0)
            opt = opts[sel_idx] if 0 <= sel_idx < len(opts) else {}
            if opt.get("cancel") or opt.get("skip") or ans.strip().lower() in ("cancel", "skip", "done / continue"):
                self._question_state = None
                self.screen.set_question(None)
                self.screen.set_status("Onboarding closed", C_DIM)
                self.screen.redraw()
                return
            key_target = opt.get("key_target")
            if key_target:
                self._question_state = None
                self.screen.set_question(None)
                self._pending_key_entry = key_target
                self.screen.set_status(f"Enter or paste your {key_target} and press Enter:", C_TOOL_Y)
                self.screen.redraw()
                return

        # Normal askUserQuestion handling
        if ans.strip().lower() == "skip":
            self._skip_question()
            return

        qs["answers"].append(ans)
        curr_q = qs["questions"][qs["index"]]
        q_text = curr_q.get("question", "")

        # Log selection into the active tool node so the choice is recorded in conversation
        if self.screen.cur_turn:
            for tid, node in list(self.screen.cur_turn.tool_nodes.items()):
                if node.name == "askUserQuestion" and not node.done:
                    self.screen.append_tool_log(tid, "stdout", f"? {q_text}\n✓ {ans}")

        qs["index"] += 1
        qs["selected_option"] = 0
        if qs["index"] < len(qs["questions"]):
            self.screen.set_question(qs)
            self.screen.set_status(f"? Question {qs['index'] + 1}/{len(qs['questions'])}", C_TOOL_Y)
        else:
            payload = {"id": qs["id"], "answers": qs["answers"]}
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status(f"✓ Selected: {ans} — continuing…", C_CYAN)
            self._send_line("<<ASK_USER_ANSWER>>" + json.dumps(payload))
        self.screen.redraw()

    def _cancel_question(self):
        if not self._question_state:
            return
        if self._question_state.get("is_model_picker"):
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status(f"✓ Active model kept: {self.active_model}", C_DIM)
            self.screen.redraw()
            return
        if self._question_state.get("is_delete_picker"):
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status("⊘ Delete cancelled", C_DIM)
            self.screen.redraw()
            return
        if self._question_state.get("is_onboarding_picker"):
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status("Onboarding dismissed", C_DIM)
            self.screen.redraw()
            return
        payload = {"id": self._question_state["id"], "cancelled": True}
        self._question_state = None
        self.screen.set_question(None)
        self.screen.set_status("⊘ Question cancelled", C_DIM)
        self._send_line("<<ASK_USER_ANSWER>>" + json.dumps(payload))
        self.screen.redraw()

    def _skip_question(self):
        if not self._question_state:
            return
        if self._question_state.get("is_model_picker"):
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status(f"✓ Active model kept: {self.active_model}", C_DIM)
            self.screen.redraw()
            return
        if self._question_state.get("is_delete_picker"):
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status("⊘ Delete cancelled", C_DIM)
            self.screen.redraw()
            return
        if self._question_state.get("is_onboarding_picker"):
            self._question_state = None
            self.screen.set_question(None)
            self.screen.set_status("Onboarding dismissed", C_DIM)
            self.screen.redraw()
            return
        qs = self._question_state
        payload = {
            "id": qs["id"],
            "answers": qs.get("answers", []) + ["Skipped"] * (len(qs.get("questions", [])) - qs.get("index", 0)),
            "skipped": True,
        }
        self._question_state = None
        self.screen.set_question(None)
        self.screen.set_status("↷ Question skipped", C_DIM)
        self._send_line("<<ASK_USER_ANSWER>>" + json.dumps(payload))
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
        self.screen.mark_interrupted()
        self.screen.set_status("⊘ Interrupted", C_DIM)
        self._send_line("<<PRIYA_INTERRUPT>>")
        self.screen.redraw()

    def _send_line(self, text):
        try:
            with self._stdin_lock:
                if self.proc and self.proc.stdin:
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
