"""

Model: mistral-large-latest (streaming chat completions, tool calling).
Protocol to priya.py is unchanged:
  <<TOOL_START>>{...}
  <<TOOL_END>>{...}
  <<TOOL_LOG>>{...}
  <<ASK_USER_QUESTION>>{...}
  <<EDIT_APPROVAL>>{...}
  <<SCHEDULED_TASK>>{...}
  <<AGENTJOB_RESULT>>{...}
  <<END>>
  (any other line = streamed model text)
"""

import os
import sys
import json
import asyncio
import traceback
import subprocess
import queue
import threading
import time
import signal
import uuid
import difflib
import hashlib
import shlex
import glob
import re
import shutil
from tools.lsp import LspManager
from tools.mcp import McpManager
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
import urllib.parse
from bs4 import BeautifulSoup

MODEL = "mistral-medium-latest"

DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.realpath(os.getcwd())
AGENTJOB_BIN = os.path.join(DIR, "tools", "job_runner.py")
ARTIFACT_BIN = os.path.join(DIR, "tools", "artifact.py")
INTERRUPT_COMMAND = "<<PRIYA_INTERRUPT>>"
CRON_MAX_LIFETIME = timedelta(days=3)


@dataclass(frozen=True)
class CronSchedule:
    minute: frozenset
    hour: frozenset
    day_of_month: frozenset
    month: frozenset
    day_of_week: frozenset
    day_of_month_wildcard: bool
    day_of_week_wildcard: bool

    def matches(self, moment):
        cron_weekday = (moment.weekday() + 1) % 7
        day_match = moment.day in self.day_of_month
        weekday_match = cron_weekday in self.day_of_week
        if not self.day_of_month_wildcard and not self.day_of_week_wildcard:
            day_ok = day_match or weekday_match
        elif not self.day_of_month_wildcard:
            day_ok = day_match
        elif not self.day_of_week_wildcard:
            day_ok = weekday_match
        else:
            day_ok = True
        return (
            moment.minute in self.minute and moment.hour in self.hour
            and moment.month in self.month and day_ok
        )


def _parse_cron_field(text, minimum, maximum, field_name, *, allow_sunday_seven=False):
    if not isinstance(text, str) or not text:
        raise ValueError(f"{field_name} is empty")
    values = set()
    for part in text.split(","):
        if not part:
            raise ValueError(f"{field_name} has an empty list item")
        base, separator, step_text = part.partition("/")
        if separator:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"{field_name} has an invalid step")
            step = int(step_text)
        else:
            step = 1
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            endpoints = base.split("-")
            if len(endpoints) != 2 or not all(item.isdigit() for item in endpoints):
                raise ValueError(f"{field_name} has an invalid range")
            start, end = map(int, endpoints)
        elif base.isdigit():
            start = int(base)
            end = maximum if separator else start
        else:
            raise ValueError(f"{field_name} has an invalid value")
        allowed_maximum = 7 if allow_sunday_seven else maximum
        if start < minimum or end > allowed_maximum or start > end:
            raise ValueError(f"{field_name} is outside {minimum}-{allowed_maximum}")
        values.update(range(start, end + 1, step))
    if allow_sunday_seven:
        values = {0 if value == 7 else value for value in values}
    return frozenset(values)


def parse_cron(expression):
    if not isinstance(expression, str):
        raise ValueError("cron must be a five-field expression")
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron must have exactly five fields: minute hour day month weekday")
    minute, hour, day_of_month, month, day_of_week = fields
    return CronSchedule(
        _parse_cron_field(minute, 0, 59, "minute"),
        _parse_cron_field(hour, 0, 23, "hour"),
        _parse_cron_field(day_of_month, 1, 31, "day of month"),
        _parse_cron_field(month, 1, 12, "month"),
        _parse_cron_field(day_of_week, 0, 6, "day of week", allow_sunday_seven=True),
        day_of_month == "*",
        day_of_week == "*",
    )


def next_cron_run(schedule, after, expires_at):
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while candidate <= expires_at:
        if schedule.matches(candidate):
            return candidate
        candidate += timedelta(minutes=1)
    return None


# ── Tool schemas (Mistral function-calling format) ────────────────────────────

def _fn(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }

def _str(description): return {"type": "string", "description": description}
def _int(description): return {"type": "integer", "description": description}
def _bool(description): return {"type": "boolean", "description": description}
def _arr(items, description): return {"type": "array", "items": items, "description": description}


TOOLS = [
    _fn("agentjob",
        "Delegate non-trivial front-end implementation in this repository to a background "
        "coding agent. Spawn returns immediately; use status and log to follow progress.",
        {
            "action": {"type": "string", "enum": ["spawn", "status", "log", "send", "stop"]},
            "task": _str("For spawn: concrete UI requirements, relevant paths, constraints."),
            "workdir": _str("Optional project directory; omit to use Priya's starting directory."),
            "job_id": _str("Job ID returned by spawn; required for status/log/send/stop."),
            "message": _str("Required for send; instructions for the running agent's next turn."),
            "cursor": _int("Optional log cursor."),
        },
        ["action"],
    ),
    _fn("artifact",
        "Create and manage a standalone browser deliverable. lifecycle: publish → start → report URL.",
        {
            "action": {"type": "string", "enum": ["publish", "list", "history", "revert", "start", "stop", "share"]},
            "name": _str("Stable lowercase slug; required for publish, history, revert."),
            "html": _str("Complete standalone HTML document with inline CSS/JS; required for publish."),
            "title": _str("Optional browser title for publish."),
            "version": _str("Version ID required for revert."),
            "port": _int("Optional local server port for start; defaults to 8765."),
        },
        ["action"],
    ),
    _fn("bash",
        "Run a bash command directly and return its output. Set background=true for long-running "
        "servers or watchers. Never cat a whole file: use head -n 16 or sed -n 'START,ENDp'.",
        {
            "command": _str("The bash command to run."),
            "timeout_s": _int("Optional. Default 60."),
            "background": _bool("Start without waiting; for long-running processes."),
        },
        ["command"],
    ),
    _fn("askUserQuestion",
        "Interactive multiple-choice prompt. Call this tool whenever you want to ask the user a question, clarify ambiguous requirements, confirm next steps, or offer choices. Never write numbered question options in chat text; always invoke askUserQuestion instead.",
        {
            "questions": {
                "type": "array",
                "description": "One to four questions to ask together.",
                "items": {
                    "type": "object",
                    "properties": {
                        "header": _str("Short category or title for the question (e.g. 'Next Step', 'File Target')."),
                        "question": _str("The decision or question the user should answer."),
                        "options": {
                            "type": "array",
                            "description": "Two to four concise choices.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": _str("Short option title."),
                                    "description": _str("Optional details on what choosing it means."),
                                },
                                "required": ["label"],
                            },
                        },
                    },
                    "required": ["question", "options"],
                },
            },
        },
        ["questions"],
    ),
    _fn("CronCreate",
        "Create a session-scoped scheduled prompt. cron is a standard five-field expression.",
        {
            "cron": _str("Numeric five-field local-time cron expression."),
            "prompt": _str("Prompt Priya should run at the scheduled time."),
            "recurring": _bool("True to repeat; false to run once and delete."),
        },
        ["cron", "prompt"],
    ),
    _fn("CronDelete", "Delete one session-scoped scheduled prompt by its job_id.",
        {"job_id": _str("ID returned by CronCreate or CronList.")}, ["job_id"]),
    _fn("CronList", "List active session-scoped scheduled prompts.", {}, []),
    _fn("EnterPlanMode",
        "Enter read-only planning mode. Write-capable tools are blocked until ExitPlanMode.", {}, []),
    _fn("ExitPlanMode", "Leave read-only planning mode.", {}, []),
    _fn("EnterWorkTree",
        "Scope Priya's file operations to an existing git worktree directory.",
        {"path": _str("Path to an existing git worktree directory.")}, ["path"]),
    _fn("ExitWorkTree", "Return Priya's tool scope to the directory from which it was started.", {}, []),
    _fn("Read",
        "Read an existing UTF-8 text file before proposing an Edit.",
        {"path": _str("Path to an existing text file.")}, ["path"]),
    _fn("Glob",
        "List files matching Unix-style patterns in Priya's current working directory.",
        {
            "patterns": _arr({"type": "string"}, "One or more relative patterns, e.g. ['src/**/*.py']."),
            "max_results": _int("Maximum files to return; defaults to 1000."),
        },
        ["patterns"],
    ),
    _fn("Grep",
        "Search file contents with a ripgrep-compatible regular expression.",
        {
            "pattern": _str("Regular expression to search for."),
            "path": _str("Optional relative file or directory to search."),
            "case_sensitive": _bool("Whether matching is case-sensitive; defaults to true."),
            "max_results": _int("Maximum matches to return; defaults to 1000."),
        },
        ["pattern"],
    ),
    _fn("LSP",
        "Query the active project's Language Server Protocol semantic model.",
        {
            "action": {"type": "string",
                        "enum": ["status", "definition", "references", "hover",
                                 "document_symbols", "workspace_symbols", "diagnostics"]},
            "path": _str("Source file path."),
            "line": _int("One-based source line."),
            "character": _int("Zero-based character offset; defaults to 0."),
            "include_declaration": _bool("Include declaration in references."),
            "query": _str("Symbol query for workspace_symbols."),
        },
        ["action"],
    ),
    _fn("ListMcpResourcesTool",
        "Discover the live catalog exposed by configured MCP servers.", {}, []),
    _fn("ReadMcpResourceTool",
        "Read the contents of a resource from a configured MCP server by URI.",
        {
            "uri": _str("URI of the resource to read, e.g. from ListMcpResourcesTool."),
            "server": _str("Optional MCP server name."),
        },
        ["uri"],
    ),
    _fn("Write",
        "Create a new file or completely overwrite an existing file with the provided UTF-8 content. "
        "Creates any necessary parent directories automatically.",
        {
            "path": _str("Path to the file to create or overwrite."),
            "content": _str("The full text content to write."),
        },
        ["path", "content"],
    ),
    _fn("WebSearch",
        "Search the web using DuckDuckGo to find real-time information, documentation, and answers.",
        {
            "query": _str("The search query."),
            "max_results": _int("Optional maximum results (default 8)."),
        },
        ["query"],
    ),
    _fn("WebFetch",
        "Fetch and extract readable plain text content from a web URL.",
        {
            "url": _str("The HTTP or HTTPS URL to fetch."),
            "max_length": _int("Optional maximum character length (default 16000)."),
        },
        ["url"],
    ),
    _fn("TaskCreate",
        "Create a session-scoped task in Priya's task tracker for structured planning and progress.",
        {
            "subject": _str("Short subject or title of the task."),
            "description": _str("Optional detailed description or sub-steps."),
        },
        ["subject"],
    ),
    _fn("TaskList",
        "List all session-scoped tasks and their current statuses.",
        {},
        [],
    ),
    _fn("TaskGet",
        "Get the full details of a session task by its ID.",
        {"task_id": _str("ID of the task, e.g. task-1.")},
        ["task_id"],
    ),
    _fn("TaskUpdate",
        "Update the status, subject, or description of a session task.",
        {
            "task_id": _str("ID of the task to update."),
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"], "description": "New status for the task."},
            "subject": _str("Optional updated subject."),
            "description": _str("Optional updated description."),
        },
        ["task_id"],
    ),
    _fn("TaskStop",
        "Cancel or stop an active session task.",
        {"task_id": _str("ID of the task to cancel.")},
        ["task_id"],
    ),
    _fn("Edit",
        "Make a targeted replacement in an existing UTF-8 file. Requires prior Read. "
        "Shows a unified diff and waits for user approval before writing.",
        {
            "path": _str("Path previously passed to Read."),
            "old_string": _str("Exact existing text to replace."),
            "new_string": _str("Replacement text."),
            "replace_all": _bool("Replace every match; defaults to false."),
        },
        ["path", "old_string", "new_string"],
    ),
]

SYSTEM_INSTRUCTION = """
You are Priya, a capable coding assistant. You have tools and are expected
to use them proactively. The user is responsible only for communicating their
intent, not for naming files, paths, patterns, commands, tools, or an execution
plan. Infer the needed work, discover the relevant project context yourself,
and carry it through until the actual requested outcome is achieved. Do not
give a guess, a hypothetical command, or a generic answer when the user's
request can be answered by inspecting or acting on their local workspace.

Format your responses in Markdown. Use headers, code blocks, bold, and lists
where they improve readability — the interface renders Markdown natively.

Tool-use policy:
- Before answering any request about files, code, configuration, tests, Git,
  commands, processes, the project state, or a previous tool/subagent result,
  use a relevant tool. Inspect first when facts are unknown.
- Choose tools yourself from the user's intent. Use Glob to map an unknown
  file set, Grep to locate symbols, Read for exact content, and bash for
  broader inspection, execution, and verification.
- bash is your direct local shell. Use it for fast inspection, editing,
  running tests, builds, commands, and verifying work.
- agentjob is Priya's implementation path for non-trivial front-end changes.
- EnterPlanMode switches Priya into a read-only analytical state.
- Edit is the preferred way to modify an existing file. Call Read first, then
  pass exact old_string and new_string.
- Write creates or completely overwrites a file directly with full content.
- WebSearch and WebFetch provide live internet searching and web page reading.
- TaskCreate, TaskList, TaskGet, TaskUpdate, and TaskStop organize multi-step work into clear tracked milestones.
- When asking the user a question, clarifying ambiguous intent, or presenting choices
  and next steps (e.g. "Would you like me to: 1. ... 2. ... 3. ..."), DO NOT write numbered
  questions or options in plain chat text. You MUST call the `askUserQuestion` tool instead.
  Priya renders an interactive UI modal for the user to select from your options.
- Report what actually happened, including relevant command/test results.

Self-sufficiency:
- Capability is not fixed: lacking a tool, library, or CLI is a solvable
  problem, not a reason to decline. Use bash to install or build what you need.
- Reply in English at all times, regardless of what language the user writes in.

The exact message <<PRIYA_INTERRUPT>> is an application control command, not
a user request: stop any response and produce no reply.
""".strip()

PIPED = not sys.stdin.isatty()
OUT_LOCK = threading.Lock()


def out(line: str):
    with OUT_LOCK:
        if PIPED:
            sys.stdout.write(line.replace("\n", " ") + "\n")
            sys.stdout.flush()
        else:
            print(line)


# ── Helpers shared with the original live_cli ─────────────────────────────────

MAX_FILE_PREVIEW_LINES = 16


def bound_plain_cat(command: str) -> str:
    if not isinstance(command, str):
        return command
    if any(token in command for token in ("|", ";", "&", ">", "<", "`", "$", "\n")):
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) < 2 or parts[0] != "cat" or any(part.startswith("-") for part in parts[1:]):
        return command
    paths_str = " ".join(shlex.quote(part) for part in parts[1:])
    return f"head -n {MAX_FILE_PREVIEW_LINES} -- {paths_str}"


def run_bash(args: dict, on_output=None, cancel_event=None, cwd=None) -> dict:
    command = args.get("command")
    if not command:
        return {"error": "bash requires 'command'"}
    command = bound_plain_cat(command)
    if cancel_event is not None and cancel_event.is_set():
        return {"error": "command interrupted"}
    if args.get("background"):
        process_dir = os.path.join(DIR, ".priya", "processes")
        process_id = f"process-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        stdout_path = os.path.join(process_dir, f"{process_id}.stdout.log")
        stderr_path = os.path.join(process_dir, f"{process_id}.stderr.log")
        metadata_path = os.path.join(process_dir, f"{process_id}.json")
        try:
            os.makedirs(process_dir, exist_ok=True)
            stdout_log = open(stdout_path, "a", encoding="utf-8")
            stderr_log = open(stderr_path, "a", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    command, shell=True, stdout=stdout_log, stderr=stderr_log,
                    text=True, start_new_session=True, cwd=cwd,
                )
            finally:
                stdout_log.close()
                stderr_log.close()
            metadata = {
                "process_id": process_id, "pid": proc.pid, "command": command,
                "cwd": cwd or os.getcwd(), "started_at": time.time(),
                "stdout_log": stdout_path, "stderr_log": stderr_path,
            }
            with open(metadata_path, "w", encoding="utf-8") as mf:
                json.dump(metadata, mf)
            return {**metadata, "metadata": metadata_path, "background": True}
        except Exception as e:
            return {"error": f"could not start background command: {e}"}
    timeout_s = args.get("timeout_s", 60)
    try:
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, start_new_session=True, cwd=cwd,
        )
        events = queue.Queue()

        def read_pipe(stream_name, stream):
            for chunk in iter(stream.readline, ""):
                events.put((stream_name, chunk))
            events.put((stream_name, None))

        for stream_name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            threading.Thread(target=read_pipe, args=(stream_name, stream), daemon=True).start()

        captured = {"stdout": "", "stderr": ""}
        limits = {"stdout": 8000, "stderr": 4000}
        closed_streams = 0
        timed_out = False
        interrupted = False
        deadline = time.monotonic() + timeout_s
        while closed_streams < 2:
            if not interrupted and cancel_event is not None and cancel_event.is_set():
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                interrupted = True
            if not timed_out and time.monotonic() >= deadline:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                timed_out = True
            try:
                stream_name, chunk = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                closed_streams += 1
                continue
            captured[stream_name] = (captured[stream_name] + chunk)[-limits[stream_name]:]
            if on_output is not None:
                on_output(stream_name, chunk.rstrip("\r\n"))
        returncode = proc.wait()
        if interrupted:
            return {"error": "command interrupted"}
        if timed_out:
            return {"error": f"command timed out after {timeout_s}s"}
        return {"exit_code": returncode, "stdout": captured["stdout"], "stderr": captured["stderr"]}
    except Exception as e:
        return {"error": str(e)}


def run_agentjob(args: dict, on_output=None) -> dict:
    action = args.get("action")
    cmd = [sys.executable, AGENTJOB_BIN, action]
    if action == "spawn":
        task = args.get("task")
        if not task:
            return {"error": "spawn requires 'task'"}
        cmd.append(task)
        if args.get("workdir"):
            cmd.append(args["workdir"])
    elif action in ("status", "log", "stop"):
        job_id = args.get("job_id")
        if not job_id:
            return {"error": f"{action} requires 'job_id'"}
        cmd.append(job_id)
        if action == "log" and args.get("cursor") is not None:
            cmd.append(str(args["cursor"]))
    elif action == "send":
        job_id = args.get("job_id")
        message = args.get("message")
        if not job_id or not message:
            return {"error": "send requires 'job_id' and 'message'"}
        cmd.extend([job_id, message])
    else:
        return {"error": f"unknown action: {action}"}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        raw = (result.stdout or "").strip()
        try:
            parsed = json.loads(raw)
            if action == "log" and on_output is not None:
                for event in parsed.get("events", []):
                    message = event.get("message")
                    if isinstance(message, str) and message:
                        on_output("agentjob", message)
            return parsed
        except json.JSONDecodeError:
            return {"raw_output": raw, "stderr": result.stderr[-500:]}
    except subprocess.TimeoutExpired:
        return {"error": "agentjob call timed out (30s)"}
    except Exception as e:
        return {"error": str(e)}


def run_artifact(args: dict) -> dict:
    action = args.get("action")
    cmd = [sys.executable, ARTIFACT_BIN, action]
    if action == "publish":
        if not args.get("name") or not args.get("html"):
            return {"error": "publish requires name and html"}
        cmd.extend([args["name"], "--html", args["html"]])
        if args.get("title"):
            cmd.extend(["--title", args["title"]])
    elif action in ("history", "revert"):
        if not args.get("name"):
            return {"error": f"{action} requires name"}
        cmd.append(args["name"])
        if action == "revert":
            if not args.get("version"):
                return {"error": "revert requires version"}
            cmd.append(args["version"])
    elif action == "start":
        if args.get("port") is not None:
            cmd.extend(["--port", str(args["port"])])
    elif action not in ("list", "stop", "share"):
        return {"error": f"unknown artifact action: {action}"}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        raw = (result.stdout or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": result.stderr.strip() or raw or f"artifact exited {result.returncode}"}
    except subprocess.TimeoutExpired:
        return {"error": "artifact call timed out (30s)"}
    except Exception as e:
        return {"error": str(e)}


_AGENTJOB_PROGRESS_KINDS = {"narration", "retry", "command_error", "failed", "stopped"}


def agentjob_progress_message(event: dict):
    if event.get("kind") not in _AGENTJOB_PROGRESS_KINDS:
        return None
    message = event.get("message")
    return message if isinstance(message, str) and message else None


# ── Main text loop ────────────────────────────────────────────────────────────

class TextLoop:
    def __init__(self):
        self._tool_event_id = 0
        self._question_event_id = 0
        self._pending_question = None
        self._edit_event_id = 0
        self._pending_edit = None
        self._read_files = {}
        self._base_workdir = SESSION_DIR
        self._current_workdir = self._base_workdir
        self._lsp = LspManager(self._current_workdir)
        self._mcp = McpManager(self._current_workdir)
        self._plan_mode = False
        self._active_bash_cancel = None
        self._cron_jobs = {}
        self._scheduled_prompts = asyncio.Queue()
        self._queued_cron_job_counts = {}
        self._cancelled_queued_cron_jobs = set()
        self._generation_active = False
        self._agentjob_watchers = {}
        self._completed_agentjobs = asyncio.Queue()
        self._interrupt_event = asyncio.Event()
        self._tasks = {}
        self._task_id_counter = 0
        # conversation history sent to Mistral on every turn
        self._messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]


    # ── agentjob watcher ──────────────────────────────────────────

    async def watch_agentjob(self, job_id, tool_event_id):
        cursor = 0
        try:
            while True:
                result = await asyncio.to_thread(run_agentjob, {
                    "action": "log", "job_id": job_id, "cursor": cursor,
                })
                if result.get("error"):
                    await self._completed_agentjobs.put({
                        "job_id": job_id, "status": "failed",
                        "reason": "monitor_error", "error": result["error"],
                    })
                    out("<<TOOL_LOG>>" + json.dumps({
                        "id": tool_event_id, "stream": "agentjob", "text": result["error"],
                    }))
                    return
                for event in result.get("events", []):
                    message = agentjob_progress_message(event)
                    if message:
                        out("<<TOOL_LOG>>" + json.dumps({
                            "id": tool_event_id, "stream": "agentjob", "text": message,
                        }))
                cursor = result.get("next_cursor", cursor)
                if result.get("status") != "running":
                    outcome = {
                        "job_id": job_id, "status": result.get("status"),
                        "reason": result.get("reason"), "error": result.get("error"),
                        "workdir": result.get("workdir"), "task": result.get("task"),
                    }
                    out("<<TOOL_LOG>>" + json.dumps({
                        "id": tool_event_id, "stream": "agentjob",
                        "text": f"Job {result.get('status')}: {result.get('reason') or 'finished'}",
                    }))
                    await self._completed_agentjobs.put(outcome)
                    return
                await asyncio.sleep(.35)
        except Exception as error:
            out("<<TOOL_LOG>>" + json.dumps({
                "id": tool_event_id, "stream": "agentjob", "text": f"Progress tailer error: {error}",
            }))
            await self._completed_agentjobs.put({
                "job_id": job_id, "status": "failed",
                "reason": "monitor_error", "error": str(error),
            })
        finally:
            self._agentjob_watchers.pop(job_id, None)

    def start_agentjob_watcher(self, job_id, tool_event_id):
        if job_id and job_id not in self._agentjob_watchers:
            self._agentjob_watchers[job_id] = asyncio.create_task(
                self.watch_agentjob(job_id, tool_event_id)
            )

    # ── Tool execution ────────────────────────────────────────────

    def _tool_blocked_by_plan_mode(self, tool_name):
        if not self._plan_mode:
            return None
        allowed = {
            "EnterPlanMode", "ExitPlanMode", "EnterWorkTree", "ExitWorkTree",
            "Read", "Glob", "Grep", "LSP", "ListMcpResourcesTool", "ReadMcpResourceTool",
            "CronList", "askUserQuestion", "WebSearch", "WebFetch",
            "TaskCreate", "TaskList", "TaskGet", "TaskUpdate", "TaskStop",
        }
        if tool_name in allowed:
            return None
        return {"error": f"{tool_name} is blocked in Plan Mode. Call ExitPlanMode first.", "plan_mode": True}

    def _resolve_tool_path(self, path, tool_name="path"):
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"{tool_name} requires a non-empty path")
        expanded = os.path.expanduser(path)
        if not os.path.isabs(expanded):
            expanded = os.path.join(self._current_workdir, expanded)
        return os.path.realpath(os.path.abspath(expanded))

    def _edit_path(self, path):
        return self._resolve_tool_path(path, "Edit")

    @staticmethod
    def _read_text_file(path):
        try:
            with open(path, "r", encoding="utf-8", newline="") as source:
                return source.read()
        except FileNotFoundError:
            raise ValueError(f"file does not exist: {path}")
        except IsADirectoryError:
            raise ValueError(f"path is a directory: {path}")
        except UnicodeDecodeError:
            raise ValueError(f"file is not valid UTF-8 text: {path}")
        except OSError as error:
            raise ValueError(f"could not read {path}: {error}")

    async def read_file(self, args):
        try:
            path = self._edit_path(args.get("path"))
            content = self._read_text_file(path)
        except ValueError as error:
            return {"error": str(error)}
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._read_files[path] = digest
        return {"path": path, "content": content, "bytes": len(content.encode("utf-8"))}

    async def request_edit_approval(self, path, diff):
        self._edit_event_id += 1
        edit_id = self._edit_event_id
        future = asyncio.get_running_loop().create_future()
        self._pending_edit = {"id": edit_id, "future": future}
        out("<<EDIT_APPROVAL>>" + json.dumps({"id": edit_id, "path": path, "diff": diff}))
        try:
            return await future
        finally:
            self._pending_edit = None

    async def edit_file(self, args):
        blocked = self._tool_blocked_by_plan_mode("Edit")
        if blocked is not None:
            return blocked
        path_arg = args.get("path")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        replace_all = args.get("replace_all", False)
        if not isinstance(old_string, str) or not old_string:
            return {"error": "Edit requires a non-empty old_string"}
        if not isinstance(new_string, str):
            return {"error": "Edit requires new_string to be a string"}
        if old_string == new_string:
            return {"error": "old_string and new_string are identical"}
        if not isinstance(replace_all, bool):
            return {"error": "Edit requires replace_all to be true or false"}
        try:
            path = self._edit_path(path_arg)
            content = self._read_text_file(path)
        except ValueError as error:
            return {"error": str(error)}
        current_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if self._read_files.get(path) != current_digest:
            return {"error": "Edit requires Read on this unchanged file first"}
        matches = content.count(old_string)
        if not matches:
            return {"error": "old_string was not found in the file"}
        if matches > 1 and not replace_all:
            return {"error": f"old_string occurs {matches} times; add context or set replace_all: true"}
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)
        diff = "".join(difflib.unified_diff(
            content.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=path, tofile=path,
        ))
        approval = await self.request_edit_approval(path, diff)
        if approval.get("cancelled") or not approval.get("approved"):
            return {"approved": False, "path": path, "changed": False}
        try:
            current = self._read_text_file(path)
        except ValueError as error:
            return {"error": str(error)}
        if current != content:
            return {"error": "file changed while awaiting approval; Read it again before editing"}
        try:
            with open(path, "w", encoding="utf-8", newline="") as destination:
                destination.write(updated)
        except OSError as error:
            return {"error": f"could not write {path}: {error}"}
        self._read_files[path] = hashlib.sha256(updated.encode("utf-8")).hexdigest()
        return {"approved": True, "path": path, "changed": True,
                "replacements": matches if replace_all else 1, "diff": diff}

    @staticmethod
    def _validate_questions(raw_questions):
        if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 4:
            return None, "askUserQuestion requires one to four questions"
        questions = []
        for index, raw in enumerate(raw_questions, start=1):
            if not isinstance(raw, dict):
                return None, f"question {index} must be an object"
            question = raw.get("question")
            options = raw.get("options")
            if not isinstance(question, str) or not question.strip():
                return None, f"question {index} needs non-empty question text"
            if not isinstance(options, list) or not 2 <= len(options) <= 4:
                return None, f"question {index} needs two to four options"
            cleaned_options = []
            labels = set()
            for option in options:
                if isinstance(option, str):
                    label = option.strip()
                    desc = option.strip()
                elif isinstance(option, dict):
                    label = str(option.get("label") or option.get("name") or option.get("text") or "").strip()
                    desc = str(option.get("description") or label).strip()
                else:
                    return None, f"question {index} has an invalid option"
                if not label:
                    return None, f"question {index} options need a non-empty label"
                normalized_label = label
                if normalized_label.casefold() in labels:
                    normalized_label = f"{label} ({len(labels) + 1})"
                labels.add(normalized_label.casefold())
                cleaned_options.append({"label": normalized_label, "description": desc})
            header = raw.get("header", f"Question {index}")
            questions.append({
                "header": header.strip() if isinstance(header, str) and header.strip() else f"Question {index}",
                "question": question.strip(),
                "options": cleaned_options,
            })
        return questions, None

    def _resolve_pending_question(self, answer):
        pending = self._pending_question
        if pending is not None and not pending["future"].done():
            pending["future"].set_result(answer)

    def _resolve_pending_edit(self, approval):
        pending = self._pending_edit
        if pending is not None and not pending["future"].done():
            pending["future"].set_result(approval)

    async def ask_user_question(self, args):
        questions, error = self._validate_questions(args.get("questions"))
        if error:
            return {"error": error}
        self._question_event_id += 1
        question_id = self._question_event_id
        future = asyncio.get_running_loop().create_future()
        self._pending_question = {"id": question_id, "future": future, "questions": questions}
        out("<<ASK_USER_QUESTION>>" + json.dumps({"id": question_id, "questions": questions}))
        try:
            response = await future
        finally:
            self._pending_question = None
        if response.get("cancelled"):
            return {"cancelled": True}
        answers = response.get("answers")
        if not isinstance(answers, list) or len(answers) != len(questions):
            return {"error": "the question response was incomplete"}
        result_answers = []
        for question, answer in zip(questions, answers):
            if not isinstance(answer, str) or not answer.strip():
                return {"error": "the question response contained an empty answer"}
            result_answers.append({"question": question["question"], "answer": answer.strip()})
        return {"answers": result_answers}

    async def run_active_bash(self, args, on_output):
        blocked = self._tool_blocked_by_plan_mode("bash")
        if blocked is not None:
            return blocked
        cancel_event = threading.Event()
        self._active_bash_cancel = cancel_event
        try:
            return await asyncio.to_thread(
                run_bash, args, on_output, cancel_event, self._current_workdir
            )
        finally:
            if self._active_bash_cancel is cancel_event:
                self._active_bash_cancel = None

    async def run_agentjob_tool(self, args, on_output):
        blocked = self._tool_blocked_by_plan_mode("agentjob")
        if blocked is not None:
            return blocked
        scoped_args = dict(args)
        if scoped_args.get("action") == "spawn" and not scoped_args.get("workdir"):
            scoped_args["workdir"] = self._current_workdir
        return await asyncio.to_thread(run_agentjob, scoped_args, on_output)

    # ── Glob / Grep / LSP / MCP ───────────────────────────────────

    @staticmethod
    def _expand_glob_braces(pattern):
        start = pattern.find("{")
        if start == -1:
            return [pattern]
        depth = 0
        for index in range(start, len(pattern)):
            character = pattern[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    body = pattern[start + 1:index]
                    choices = body.split(",")
                    if not all(choices):
                        return [pattern]
                    expanded = []
                    for choice in choices:
                        expanded.extend(TextLoop._expand_glob_braces(
                            pattern[:start] + choice + pattern[index + 1:]
                        ))
                    return expanded
        return [pattern]

    def _validate_glob_pattern(self, pattern):
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("Glob patterns must be non-empty strings")
        if os.path.isabs(pattern) or any(part == ".." for part in pattern.split("/")):
            raise ValueError("Glob patterns must be relative and cannot contain '..'")
        return pattern

    def _glob_matches(self, pattern):
        matches = set()
        for expanded in self._expand_glob_braces(pattern):
            search_pattern = os.path.join(self._current_workdir, expanded)
            for candidate in glob.glob(search_pattern, recursive=True):
                resolved = os.path.realpath(os.path.abspath(candidate))
                try:
                    in_scope = os.path.commonpath([self._current_workdir, resolved]) == self._current_workdir
                except ValueError:
                    in_scope = False
                if in_scope and os.path.isfile(resolved):
                    matches.add(resolved)
        return matches

    async def glob_files(self, args):
        patterns = args.get("patterns")
        max_results = args.get("max_results", 1000)
        if not isinstance(patterns, list) or not patterns:
            return {"error": "Glob requires a non-empty patterns array"}
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 10000:
            return {"error": "max_results must be an integer from 1 to 10000"}
        try:
            positive_patterns = []
            negative_patterns = []
            for raw_pattern in patterns:
                is_negative = isinstance(raw_pattern, str) and raw_pattern.startswith("!")
                pattern = raw_pattern[1:] if is_negative else raw_pattern
                pattern = self._validate_glob_pattern(pattern)
                (negative_patterns if is_negative else positive_patterns).append(pattern)
        except ValueError as error:
            return {"error": str(error)}
        if not positive_patterns:
            return {"error": "Glob requires at least one non-negated pattern"}
        files = set()
        for pattern in positive_patterns:
            files.update(self._glob_matches(pattern))
        for pattern in negative_patterns:
            files.difference_update(self._glob_matches(pattern))
        files = sorted(files)
        return {"files": files[:max_results], "count": len(files), "truncated": len(files) > max_results,
                "workdir": self._current_workdir}

    def _resolve_grep_scope(self, path):
        if path is None:
            return self._current_workdir
        resolved = self._resolve_tool_path(path, "Grep path")
        try:
            in_scope = os.path.commonpath([self._current_workdir, resolved]) == self._current_workdir
        except ValueError:
            in_scope = False
        if not in_scope:
            raise ValueError("Grep path must be inside the current working directory")
        if not os.path.exists(resolved):
            raise ValueError(f"Grep path does not exist: {resolved}")
        return resolved

    def _run_grep_python(self, pattern, scope, case_sensitive, max_results):
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error:
            try:
                regex = re.compile(re.escape(pattern), flags)
            except re.error as error:
                return {"error": f"Invalid grep pattern: {error}"}

        matches = []
        truncated = False
        ignore_dirs = {
            ".git", ".svn", ".hg", "node_modules", "__pycache__",
            ".venv", "venv", ".idea", ".vscode", ".priya", "dist",
            "build", ".next", ".cache"
        }

        files_to_scan = []
        if os.path.isfile(scope):
            files_to_scan.append(scope)
        elif os.path.isdir(scope):
            for root, dirs, files in os.walk(scope):
                dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
                for file_name in files:
                    files_to_scan.append(os.path.join(root, file_name))
        else:
            return {"error": f"Grep target does not exist: {scope}"}

        for file_path in files_to_scan:
            try:
                with open(file_path, "rb") as bf:
                    chunk = bf.read(1024)
                    if b"\x00" in chunk:
                        continue
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_number, line in enumerate(f, start=1):
                        if regex.search(line):
                            resolved = self._resolve_tool_path(file_path, "Grep result")
                            matches.append({
                                "path": resolved,
                                "line_number": line_number,
                                "line": line.rstrip("\r\n")
                            })
                            if len(matches) > max_results:
                                matches.pop()
                                truncated = True
                                break
            except (OSError, UnicodeDecodeError):
                continue
            if truncated:
                break

        return {"matches": matches, "count": len(matches), "truncated": truncated, "scope": scope}

    def _run_grep(self, pattern, scope, case_sensitive, max_results):
        if not shutil.which("rg"):
            return self._run_grep_python(pattern, scope, case_sensitive, max_results)
        command = ["rg", "--json", "--no-messages"]
        if not case_sensitive:
            command.append("--ignore-case")
        command.extend(["--", pattern, scope])
        matches = []
        truncated = False
        try:
            process = subprocess.Popen(command, cwd=self._current_workdir,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError:
            return self._run_grep_python(pattern, scope, case_sensitive, max_results)
        try:
            for raw_event in process.stdout:
                event = json.loads(raw_event)
                if event.get("type") != "match":
                    continue
                data = event.get("data", {})
                path_text = data.get("path", {}).get("text")
                line_text = data.get("lines", {}).get("text")
                line_number = data.get("line_number")
                if not isinstance(path_text, str) or not isinstance(line_text, str):
                    continue
                path = self._resolve_tool_path(path_text, "Grep result")
                matches.append({"path": path, "line_number": line_number, "line": line_text.rstrip("\r\n")})
                if len(matches) > max_results:
                    matches.pop()
                    truncated = True
                    process.terminate()
                    break
            stderr = process.stderr.read()
            returncode = process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
            process.stdout.close()
            process.stderr.close()
        if returncode > 1 and not truncated:
            return {"error": stderr.strip() or "Grep failed"}
        return {"matches": matches, "count": len(matches), "truncated": truncated, "scope": scope}

    async def grep_files(self, args):
        pattern = args.get("pattern")
        case_sensitive = args.get("case_sensitive", True)
        max_results = args.get("max_results", 1000)
        if not isinstance(pattern, str) or not pattern:
            return {"error": "Grep requires a non-empty pattern"}
        if not isinstance(case_sensitive, bool):
            return {"error": "case_sensitive must be true or false"}
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 10000:
            return {"error": "max_results must be an integer from 1 to 10000"}
        try:
            scope = self._resolve_grep_scope(args.get("path"))
        except ValueError as error:
            return {"error": str(error)}
        return await asyncio.to_thread(self._run_grep, pattern, scope, case_sensitive, max_results)

    async def lsp_query(self, args):
        action = args.get("action")
        if not isinstance(action, str):
            return {"error": "LSP requires an action"}
        try:
            return await asyncio.to_thread(self._lsp.query, args, self._resolve_tool_path)
        except (ValueError, OSError, RuntimeError, UnicodeDecodeError) as error:
            return {"error": str(error)}

    async def list_mcp_resources(self, _args):
        return await asyncio.to_thread(self._mcp.list_resources)

    async def read_mcp_resource(self, args):
        uri = args.get("uri")
        server_name = args.get("server")
        if not isinstance(uri, str) or not uri.strip():
            return {"error": "ReadMcpResourceTool requires a uri"}
        return await asyncio.to_thread(self._mcp.read_resource, uri, server_name)

    async def write_file(self, args):
        blocked = self._tool_blocked_by_plan_mode("Write")
        if blocked is not None:
            return blocked
        path_arg = args.get("path")
        content = args.get("content")
        if not isinstance(path_arg, str) or not path_arg.strip():
            return {"error": "Write requires a non-empty path"}
        if content is None:
            return {"error": "Write requires content string"}
        try:
            resolved = self._resolve_tool_path(path_arg, "Write")
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            self._read_files[resolved] = digest
            return {
                "path": resolved,
                "bytes_written": len(content.encode("utf-8")),
                "lines_written": len(content.splitlines()),
                "success": True,
            }
        except Exception as e:
            return {"error": f"could not write {path_arg}: {e}"}

    def _run_web_search(self, query, max_results=8):
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                return {"error": f"Search engine returned HTTP {resp.status_code}", "results": []}

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for item in soup.select(".result__body"):
                title_elem = item.select_one(".result__title a")
                snippet_elem = item.select_one(".result__snippet")
                if not title_elem:
                    continue
                raw_url = title_elem.get("href", "")
                parsed = urllib.parse.urlparse(raw_url)
                qs = urllib.parse.parse_qs(parsed.query)
                clean_url = qs.get("uddg", [raw_url])[0]
                title = title_elem.get_text(strip=True)
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                if clean_url and title:
                    results.append({
                        "title": title,
                        "url": clean_url,
                        "snippet": snippet,
                    })
                if len(results) >= max_results:
                    break

            return {
                "query": query,
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"error": f"Web search failed: {e}", "results": []}

    async def web_search(self, args):
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"error": "WebSearch requires a search query"}
        max_results = int(args.get("max_results", 8))
        return await asyncio.to_thread(self._run_web_search, query, max_results)

    def _run_web_fetch(self, url, max_length=16000):
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" in content_type:
                soup = BeautifulSoup(resp.text, "html.parser")
                for s in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "svg"]):
                    s.extract()
                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                lines = [line.strip() for line in soup.get_text().splitlines() if line.strip()]
                text = "\n".join(lines)
            else:
                title = ""
                text = resp.text

            truncated = False
            if len(text) > max_length:
                text = text[:max_length]
                truncated = True

            return {
                "url": resp.url,
                "title": title,
                "status_code": resp.status_code,
                "content": text,
                "truncated": truncated,
                "length": len(text),
            }
        except Exception as e:
            return {"error": f"Failed to fetch {url}: {e}"}

    async def web_fetch(self, args):
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return {"error": "WebFetch requires a valid url"}
        max_length = int(args.get("max_length", 16000))
        return await asyncio.to_thread(self._run_web_fetch, url, max_length)

    async def task_create(self, args):
        subject = args.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return {"error": "TaskCreate requires a subject"}
        self._task_id_counter += 1
        tid = f"task-{self._task_id_counter}"
        task = {
            "id": tid,
            "subject": subject.strip(),
            "description": args.get("description", "").strip(),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self._tasks[tid] = task
        return {"task": task, "message": f"Created task {tid}: {subject.strip()}"}

    async def task_list(self, _args):
        return {
            "tasks": list(self._tasks.values()),
            "count": len(self._tasks),
        }

    async def task_get(self, args):
        tid = args.get("task_id")
        if not tid or tid not in self._tasks:
            return {"error": f"Task '{tid}' not found"}
        return {"task": self._tasks[tid]}

    async def task_update(self, args):
        tid = args.get("task_id")
        if not tid or tid not in self._tasks:
            return {"error": f"Task '{tid}' not found"}
        task = self._tasks[tid]
        if "status" in args:
            st = args["status"]
            if st not in ("pending", "in_progress", "completed", "cancelled"):
                return {"error": f"Invalid status '{st}', must be pending|in_progress|completed|cancelled"}
            task["status"] = st
        if "subject" in args and str(args["subject"]).strip():
            task["subject"] = str(args["subject"]).strip()
        if "description" in args:
            task["description"] = str(args["description"]).strip()
        task["updated_at"] = datetime.now().isoformat()
        return {"task": task, "message": f"Updated task {tid}"}

    async def task_stop(self, args):
        tid = args.get("task_id")
        if not tid or tid not in self._tasks:
            return {"error": f"Task '{tid}' not found"}
        task = self._tasks[tid]
        task["status"] = "cancelled"
        task["updated_at"] = datetime.now().isoformat()
        return {"task": task, "message": f"Stopped task {tid}"}

    # ── WorkTree / PlanMode ───────────────────────────────────────

    def _resolve_worktree_path(self, path):
        resolved = self._resolve_tool_path(path, "EnterWorkTree")
        if not os.path.isdir(resolved):
            raise ValueError(f"worktree does not exist: {resolved}")
        try:
            inside = subprocess.run(
                ["git", "-C", resolved, "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception as error:
            raise ValueError(f"could not inspect worktree {resolved}: {error}")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            detail = inside.stderr.strip() or inside.stdout.strip() or "not a git worktree"
            raise ValueError(f"{resolved} is not a git worktree: {detail}")
        return resolved

    async def enter_plan_mode(self, _args):
        self._plan_mode = True
        return {"plan_mode": True, "workdir": self._current_workdir,
                "message": "Priya is now in read-only Plan Mode."}

    async def exit_plan_mode(self, _args):
        self._plan_mode = False
        return {"plan_mode": False, "workdir": self._current_workdir, "message": "Priya has left Plan Mode."}

    async def enter_worktree(self, args):
        try:
            path = self._resolve_worktree_path(args.get("path"))
        except ValueError as error:
            return {"error": str(error)}
        self._current_workdir = path
        self._read_files = {}
        self._lsp.reset(path)
        self._mcp.reset(path)
        return {"worktree": path, "workdir": path, "plan_mode": self._plan_mode}

    async def exit_worktree(self, _args):
        self._current_workdir = self._base_workdir
        self._read_files = {}
        self._lsp.reset(self._base_workdir)
        self._mcp.reset(self._base_workdir)
        return {"worktree": None, "workdir": self._current_workdir, "plan_mode": self._plan_mode}

    # ── Cron ─────────────────────────────────────────────────────

    @staticmethod
    def _cron_job_view(job):
        return {
            "job_id": job["job_id"], "cron": job["cron"], "prompt": job["prompt"],
            "recurring": job["recurring"],
            "created_at": job["created_at"].isoformat(),
            "expires_at": job["expires_at"].isoformat(),
            "next_run": job["next_run"].isoformat() if job["next_run"] else None,
            "timezone": job["timezone"],
        }

    def _expire_cron_jobs(self, now):
        for job_id, job in list(self._cron_jobs.items()):
            if now >= job["expires_at"]:
                del self._cron_jobs[job_id]

    async def _queue_cron_job(self, job):
        job_id = job["job_id"]
        self._queued_cron_job_counts[job_id] = self._queued_cron_job_counts.get(job_id, 0) + 1
        await self._scheduled_prompts.put(job.copy())

    def _finish_queued_cron_job(self, job_id):
        remaining = self._queued_cron_job_counts.get(job_id, 0) - 1
        if remaining > 0:
            self._queued_cron_job_counts[job_id] = remaining
            return
        self._queued_cron_job_counts.pop(job_id, None)
        self._cancelled_queued_cron_jobs.discard(job_id)

    async def cron_create(self, args):
        blocked = self._tool_blocked_by_plan_mode("CronCreate")
        if blocked is not None:
            return blocked
        cron = args.get("cron")
        prompt = args.get("prompt")
        recurring = args.get("recurring", True)
        if not isinstance(prompt, str) or not prompt.strip():
            return {"error": "CronCreate requires a non-empty prompt"}
        if not isinstance(recurring, bool):
            return {"error": "CronCreate requires recurring to be true or false"}
        try:
            schedule = parse_cron(cron)
        except ValueError as error:
            return {"error": str(error)}
        now = datetime.now().astimezone()
        expires_at = now + CRON_MAX_LIFETIME
        next_run = next_cron_run(schedule, now, expires_at)
        if next_run is None:
            return {"error": "cron has no matching time before the three-day session expiry"}
        job_id = f"cron-{uuid.uuid4().hex[:8]}"
        job = {
            "job_id": job_id, "cron": cron.strip(), "schedule": schedule,
            "prompt": prompt.strip(), "recurring": recurring,
            "created_at": now, "expires_at": expires_at, "next_run": next_run,
            "timezone": now.tzname() or str(now.tzinfo),
        }
        self._cron_jobs[job_id] = job
        return self._cron_job_view(job)

    async def cron_delete(self, args):
        blocked = self._tool_blocked_by_plan_mode("CronDelete")
        if blocked is not None:
            return blocked
        job_id = args.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return {"error": "CronDelete requires job_id"}
        self._expire_cron_jobs(datetime.now().astimezone())
        job = self._cron_jobs.pop(job_id, None)
        queued = self._queued_cron_job_counts.get(job_id, 0) > 0
        if job is None and not queued:
            return {"error": f"no active cron job named {job_id}"}
        if queued:
            self._cancelled_queued_cron_jobs.add(job_id)
        return {"deleted": True, "job_id": job_id}

    async def cron_list(self, _args):
        now = datetime.now().astimezone()
        self._expire_cron_jobs(now)
        jobs = sorted(self._cron_jobs.values(), key=lambda job: job["next_run"])
        return {"jobs": [self._cron_job_view(job) for job in jobs], "timezone": now.tzname() or str(now.tzinfo)}

    # ── Mistral streaming inference ───────────────────────────────

    async def _call_mistral_and_dispatch(self, user_text: str):
        """
        Stream from /v1/conversations via SSE (Mistral Conversations API).
        Text chunks reach the UI immediately; tool calls accumulate then execute.
        """
        self._messages.append({"role": "user", "content": user_text})

        while True:
            text_chunks    = []
            tool_calls_acc = {}   # tool_call_id → {id, name, arguments}
            line_buf       = ""

            # Extract system message → instructions, convert history → inputs
            instructions = ""
            inputs = []
            for msg in self._messages:
                role = msg.get("role")
                if role == "system":
                    instructions = msg.get("content") or ""
                elif role == "assistant" and msg.get("tool_calls"):
                    # assistant tool call → one function.call entry per call
                    for tc in msg["tool_calls"]:
                        inputs.append({
                            "type": "function.call",
                            "tool_call_id": tc["id"],
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        })
                elif role == "tool":
                    # tool result → function.result (result not content)
                    inputs.append({
                        "type": "function.result",
                        "tool_call_id": msg["tool_call_id"],
                        "result": msg["content"],
                    })
                elif role == "assistant":
                    inputs.append({"type": "message.output", "role": "assistant", "content": msg.get("content") or ""})
                else:
                    inputs.append(msg)

            _headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {os.environ.get('MISTRAL_API_KEY', '')}",
            }
            _body = {
                "model": MODEL,
                "inputs": inputs,
                "instructions": instructions,
                "tools": TOOLS,
                "stream": True,
                "completion_args": {
                    "temperature": 0.7,
                    "max_tokens": 4096,
                    "top_p": 1,
                    "reasoning_effort": "high",
                },
            }

            # Bridge blocking SSE iterator → asyncio queue
            event_q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def _do_stream(h=_headers, b=_body):
                import requests as _req
                delay = 1.0
                for _attempt in range(8):
                    try:
                        resp = _req.post(
                            "https://api.mistral.ai/v1/conversations",
                            headers=h, json=b, stream=True, timeout=120,
                        )
                        if resp.status_code == 429:
                            wait = float(resp.headers.get("Retry-After") or delay)
                            print(f"[rate-limit] 429, retry in {wait:.1f}s", file=sys.stderr)
                            time.sleep(wait)
                            delay = min(delay * 2, 30.0)
                            continue
                        resp.raise_for_status()
                        for raw in resp.iter_lines():
                            if raw:
                                loop.call_soon_threadsafe(event_q.put_nowait, ("line", raw))
                        break
                    except Exception as exc:
                        loop.call_soon_threadsafe(event_q.put_nowait, ("error", str(exc)))
                        return
                loop.call_soon_threadsafe(event_q.put_nowait, ("error", "Rate limit: all 8 retries exhausted"))

            threading.Thread(target=_do_stream, daemon=True).start()

            while True:
                if self._interrupt_event.is_set():
                    self._interrupt_event.clear()
                    if line_buf.strip():
                        out(line_buf)
                    out("<<END>>")
                    return

                kind, data = await event_q.get()

                if kind == "error":
                    raise RuntimeError(data)
                if kind == "done":
                    break

                if not data.startswith(b"data: "):
                    continue
                payload = data[6:]
                if payload == b"[DONE]":
                    break

                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                event_type = chunk.get("type", "")

                # Text / Reasoning delta — conversations API
                if event_type == "message.output.delta":
                    content_raw = chunk.get("content") or ""
                    reasoning_piece = ""
                    text_piece = ""

                    if isinstance(content_raw, dict):
                        c_type = content_raw.get("type", "")
                        if c_type in ("thinking", "reasoning", "thought"):
                            thinking_arr = content_raw.get("thinking")
                            if isinstance(thinking_arr, list):
                                for item in thinking_arr:
                                    if isinstance(item, dict):
                                        reasoning_piece += item.get("text", "")
                                    else:
                                        reasoning_piece += str(item)
                            elif isinstance(thinking_arr, str):
                                reasoning_piece += thinking_arr
                            else:
                                reasoning_piece += content_raw.get("text", "")
                        elif c_type == "text":
                            text_piece += content_raw.get("text", "")
                        else:
                            text_piece += content_raw.get("text", "") or ""
                    elif isinstance(content_raw, list):
                        for c in content_raw:
                            if isinstance(c, dict):
                                c_type = c.get("type", "")
                                if c_type in ("thinking", "reasoning", "thought"):
                                    thinking_arr = c.get("thinking")
                                    if isinstance(thinking_arr, list):
                                        for item in thinking_arr:
                                            if isinstance(item, dict):
                                                reasoning_piece += item.get("text", "")
                                            else:
                                                reasoning_piece += str(item)
                                    elif isinstance(thinking_arr, str):
                                        reasoning_piece += thinking_arr
                                    else:
                                        reasoning_piece += c.get("text", "")
                                else:
                                    text_piece += c.get("text", "")
                            else:
                                text_piece += str(c)
                    elif isinstance(content_raw, str):
                        text_piece = content_raw

                    direct_reasoning = chunk.get("reasoning_content") or chunk.get("thinking") or chunk.get("reasoning")
                    if isinstance(direct_reasoning, dict):
                        direct_reasoning = direct_reasoning.get("text", "")
                    elif isinstance(direct_reasoning, list):
                        direct_reasoning = "".join(x.get("text", str(x)) if isinstance(x, dict) else str(x) for x in direct_reasoning)
                    elif direct_reasoning and not isinstance(direct_reasoning, str):
                        direct_reasoning = str(direct_reasoning)

                    if direct_reasoning:
                        reasoning_piece += direct_reasoning

                    if reasoning_piece:
                        out("<<THINKING>>" + json.dumps({"text": reasoning_piece}))

                    if text_piece:
                        text_chunks.append(text_piece)
                        line_buf += text_piece
                        while "\n" in line_buf:
                            nl = line_buf.index("\n")
                            out(line_buf[:nl])
                            line_buf = line_buf[nl + 1:]

                # Function/tool call delta — conversations API
                elif event_type == "function.call.delta":
                    tc_id = chunk.get("tool_call_id") or chunk.get("id") or ""
                    if tc_id not in tool_calls_acc:
                        tool_calls_acc[tc_id] = {"id": tc_id, "name": "", "arguments": ""}
                    if chunk.get("name"):
                        tool_calls_acc[tc_id]["name"] = chunk["name"]
                    if chunk.get("arguments"):
                        tool_calls_acc[tc_id]["arguments"] += chunk["arguments"]

                elif event_type == "conversation.response.done":
                    break

                elif event_type == "conversation.response.error":
                    raise RuntimeError(chunk.get("message", "conversation error"))

            # Flush any trailing partial line
            if line_buf.strip():
                out(line_buf)

            full_text = "".join(text_chunks)

            # No tool calls → turn is done
            if not tool_calls_acc:
                if full_text:
                    self._messages.append({"role": "assistant", "content": full_text})
                out("<<END>>")
                return

            # Tool calls → append to history, execute, loop
            assistant_tool_calls = [
                {
                    "id": tc["id"] or f"call-{i}",
                    "type": "function",
                    "function": {
                        "name":      tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for i, tc in enumerate(tool_calls_acc.values())
            ]
            self._messages.append({
                "role": "assistant",
                "content": full_text or None,
                "tool_calls": assistant_tool_calls,
            })

            tool_results = []
            for tc in tool_calls_acc.values():
                name = tc["name"]
                try:
                    args_dict = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args_dict = {}

                detail = (args_dict.get("path")
                          or args_dict.get("file_path")
                          or args_dict.get("pattern")
                          or args_dict.get("query")
                          or args_dict.get("command")
                          or args_dict.get("task")
                          or args_dict.get("subject")
                          or args_dict.get("url")
                          or args_dict.get("uri")
                          or args_dict.get("action"))
                if not detail and args_dict:
                    vals = [str(v) for v in args_dict.values() if isinstance(v, (str, int, float, bool))]
                    detail = " ".join(vals)[:80] if vals else ""
                detail = detail or ""
                home = os.path.expanduser("~")
                if home and home in detail:
                    detail = detail.replace(home, "~")
                self._tool_event_id += 1
                tool_event_id = self._tool_event_id

                out("<<TOOL_START>>" + json.dumps({"id": tool_event_id, "name": name, "detail": detail}))

                def emit_tool_output(stream_name, text, event_id=tool_event_id):
                    if text:
                        out("<<TOOL_LOG>>" + json.dumps({
                            "id": event_id, "stream": stream_name, "text": text,
                        }))

                blocked = self._tool_blocked_by_plan_mode(name)
                if blocked is not None:
                    result = blocked
                elif name == "agentjob":
                    result = await self.run_agentjob_tool(args_dict, emit_tool_output)
                    if args_dict.get("action") == "spawn":
                        self.start_agentjob_watcher(result.get("job_id"), tool_event_id)
                elif name == "artifact":
                    result = await asyncio.to_thread(run_artifact, args_dict)
                elif name == "bash":
                    result = await self.run_active_bash(args_dict, emit_tool_output)
                elif name == "askUserQuestion":
                    result = await self.ask_user_question(args_dict)
                elif name == "CronCreate":
                    result = await self.cron_create(args_dict)
                elif name == "CronDelete":
                    result = await self.cron_delete(args_dict)
                elif name == "CronList":
                    result = await self.cron_list(args_dict)
                elif name == "EnterPlanMode":
                    result = await self.enter_plan_mode(args_dict)
                elif name == "ExitPlanMode":
                    result = await self.exit_plan_mode(args_dict)
                elif name == "EnterWorkTree":
                    result = await self.enter_worktree(args_dict)
                elif name == "ExitWorkTree":
                    result = await self.exit_worktree(args_dict)
                elif name == "Read":
                    result = await self.read_file(args_dict)
                elif name == "Write":
                    result = await self.write_file(args_dict)
                elif name == "WebSearch":
                    result = await self.web_search(args_dict)
                elif name == "WebFetch":
                    result = await self.web_fetch(args_dict)
                elif name == "TaskCreate":
                    result = await self.task_create(args_dict)
                elif name == "TaskList":
                    result = await self.task_list(args_dict)
                elif name == "TaskGet":
                    result = await self.task_get(args_dict)
                elif name == "TaskUpdate":
                    result = await self.task_update(args_dict)
                elif name == "TaskStop":
                    result = await self.task_stop(args_dict)
                elif name == "Glob":
                    result = await self.glob_files(args_dict)
                elif name == "Grep":
                    result = await self.grep_files(args_dict)
                elif name == "LSP":
                    result = await self.lsp_query(args_dict)
                elif name == "ListMcpResourcesTool":
                    result = await self.list_mcp_resources(args_dict)
                elif name == "ReadMcpResourceTool":
                    result = await self.read_mcp_resource(args_dict)
                elif name == "Edit":
                    result = await self.edit_file(args_dict)
                else:
                    result = {"error": f"unknown tool {name}"}

                print(f"TOOL: {name} → {json.dumps(result)[:200]}", file=sys.stderr)
                out("<<TOOL_END>>" + json.dumps({"id": tool_event_id, "name": name, "result": result}))

                # chat completions format uses role="tool"
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"] or f"call-{i}",
                    "content": json.dumps(result),
                })

            self._messages.extend(tool_results)
            await asyncio.sleep(0.5)

        # ── Scheduler ─────────────────────────────────────────────────

    async def scheduler_loop(self):
        while True:
            now = datetime.now().astimezone()
            self._expire_cron_jobs(now)
            for job_id, job in list(self._cron_jobs.items()):
                if now < job["next_run"]:
                    continue
                await self._queue_cron_job(job)
                if job["recurring"]:
                    job["next_run"] = next_cron_run(job["schedule"], job["next_run"], job["expires_at"])
                    if job["next_run"] is None:
                        del self._cron_jobs[job_id]
                else:
                    del self._cron_jobs[job_id]

            if not self._generation_active and not self._completed_agentjobs.empty():
                job = await self._completed_agentjobs.get()
                if not job.get("ui_notified"):
                    out("<<AGENTJOB_RESULT>>" + json.dumps(job))
                    job["ui_notified"] = True
                self._generation_active = True
                instruction = (
                    f"[Agent job {job['job_id']} finished with status {job['status']}; "
                    f"reason: {job.get('reason')}; workdir: {job.get('workdir')}. "
                    f"Original task: {job.get('task')}\n"
                    "Inspect the changed files, run relevant tests, and report the actual result. "
                    "If it failed, take over now using local tools and complete the task yourself.]"
                )
                try:
                    await self._call_mistral_and_dispatch(instruction)
                except Exception as error:
                    print(f"agentjob completion dispatch error: {error}", file=sys.stderr)
                finally:
                    self._generation_active = False

            elif not self._generation_active and not self._scheduled_prompts.empty():
                job = self._scheduled_prompts.get_nowait()
                cancelled = job["job_id"] in self._cancelled_queued_cron_jobs
                self._finish_queued_cron_job(job["job_id"])
                if not cancelled and now < job["expires_at"]:
                    out("<<SCHEDULED_TASK>>" + json.dumps({"job_id": job["job_id"], "prompt": job["prompt"]}))
                    self._generation_active = True
                    try:
                        await self._call_mistral_and_dispatch(
                            f"[Scheduled job {job['job_id']}] {job['prompt']}"
                        )
                    except Exception as error:
                        print(f"scheduled job dispatch error: {error}", file=sys.stderr)
                    finally:
                        self._generation_active = False

            await asyncio.sleep(1)

    # ── stdin reader ──────────────────────────────────────────────

    async def send_text(self):
        while True:
            if PIPED:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    await asyncio.sleep(1)
                    continue
                text = line.rstrip("\n")
            else:
                text = await asyncio.to_thread(input, "message > ")

            if text == INTERRUPT_COMMAND:
                self._interrupt_event.set()
                if self._active_bash_cancel is not None:
                    self._active_bash_cancel.set()
                self._resolve_pending_question({"cancelled": True})
                self._resolve_pending_edit({"cancelled": True})
                continue

            if text.lower() == "q":
                break

            if text.startswith("<<ASK_USER_ANSWER>>"):
                try:
                    answer = json.loads(text[len("<<ASK_USER_ANSWER>>"):])
                except json.JSONDecodeError:
                    print("invalid askUserQuestion answer", file=sys.stderr)
                    continue
                if self._pending_question is None:
                    print("received answer with no pending question", file=sys.stderr)
                    continue
                if answer.get("id") != self._pending_question["id"]:
                    print("received answer for a different question", file=sys.stderr)
                    continue
                self._resolve_pending_question(answer)
                continue

            if text.startswith("<<EDIT_APPROVAL>>"):
                try:
                    approval = json.loads(text[len("<<EDIT_APPROVAL>>"):])
                except json.JSONDecodeError:
                    print("invalid Edit approval", file=sys.stderr)
                    continue
                if not isinstance(approval, dict):
                    continue
                if self._pending_edit is None:
                    print("received Edit approval with no pending edit", file=sys.stderr)
                    continue
                if approval.get("id") != self._pending_edit["id"]:
                    print("received Edit approval for a different edit", file=sys.stderr)
                    continue
                self._resolve_pending_edit(approval)
                continue

            if not text:
                continue

            self._generation_active = True
            try:
                await self._call_mistral_and_dispatch(text)
            except Exception as error:
                traceback.print_exc()
                out(f"Priya error: {error}")
                out("<<END>>")
            finally:
                self._generation_active = False

    async def run(self):
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.send_text())
                tg.create_task(self.scheduler_loop())
        except* asyncio.CancelledError:
            pass
        except* Exception as eg:
            traceback.print_exception(eg)
        finally:
            self._lsp.reset()
            self._mcp.reset()


if __name__ == "__main__":
    main = TextLoop()
    asyncio.run(main.run())
