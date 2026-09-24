#!/usr/bin/env python3
"""Repository-local background runner used by Priya's ``agentjob`` tool.

Jobs, logs, and steering messages are kept below ``.priya/jobs`` so a cloned
repository contains every executable component. Runtime job data is ignored by
Git; the runner itself is versioned in ``tools/``.
"""

import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = Path(os.environ.get("PRIYA_JOBS_DIR", PROJECT_ROOT / ".priya" / "jobs"))
PYTHON = sys.executable
MODEL = "gemini-3.5-flash-lite"
MAX_TURNS = 60
SHELL_TIMEOUT_SECONDS = 120
MAX_FILE_PREVIEW_LINES = 16
MAX_EVENT_MESSAGE_CHARS = 4000
MAX_API_RETRIES = 3
JOB_IDLE_TIMEOUT_SECONDS = 300


def paths(job_id):
    base = JOBS_DIR / job_id
    return {
        "base": base,
        "status": base / "status.json",
        "log": base / "log.txt",
        "events": base / "events.jsonl",
        "input_queue": base / "input_queue",
        "pid": base / "pid",
    }


def write_status(job_id, **fields):
    p = paths(job_id)
    current = json.loads(p["status"].read_text()) if p["status"].exists() else {}
    current.update(fields)
    current["updated_at"] = time.time()
    p["status"].write_text(json.dumps(current, indent=2))


def emit(value):
    print(json.dumps(value), flush=True)


def record_event(job_id, kind, message, *, echo=None, **extra):
    """Append one durable, UI-friendly progress event.

    The worker's stdout is deliberately still retained as a human-readable
    transcript, but consumers should tail this journal.  Unlike the old log it
    has a cursor, so polling never has to redisplay the whole agent transcript.
    """
    p = paths(job_id)
    message = str(message)
    if len(message) > MAX_EVENT_MESSAGE_CHARS:
        message = message[:MAX_EVENT_MESSAGE_CHARS] + "\n… [truncated]"
    state = json.loads(p["status"].read_text())
    cursor = state.get("event_cursor", 0) + 1
    event = {"cursor": cursor, "time": time.time(), "kind": kind, "message": message, **extra}
    with open(p["events"], "a", encoding="utf-8") as events:
        events.write(json.dumps(event) + "\n")
        events.flush()
    write_status(job_id, event_cursor=cursor, last_event=message)
    # CLI commands return a single JSON document.  Only the detached worker
    # writes the human-readable journal line to its stdout transcript.
    if echo is None:
        echo = os.environ.get("PRIYA_AGENT_JOB_ID") == job_id
    if echo:
        print(f"[{kind}] {message}", flush=True)
    return event


def spawn(task, workdir=None):
    job_id = uuid.uuid4().hex[:10]
    p = paths(job_id)
    p["base"].mkdir(parents=True)
    p["input_queue"].mkdir()
    resolved_workdir = Path(workdir).expanduser().resolve() if workdir else PROJECT_ROOT
    if not resolved_workdir.is_dir():
        emit({"error": f"workdir does not exist: {resolved_workdir}"})
        return

    write_status(
        job_id, status="running", reason=None, turn=0, task=task,
        workdir=str(resolved_workdir), created_at=time.time(), event_cursor=0,
    )
    p["log"].write_text("")
    p["events"].write_text("")
    env = os.environ.copy()
    env["PRIYA_AGENT_JOB_ID"] = job_id
    with open(p["log"], "a") as log:
        proc = subprocess.Popen(
            [PYTHON, str(Path(__file__).resolve()), "_worker", job_id],
            stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True,
        )
    p["pid"].write_text(str(proc.pid))
    record_event(job_id, "started", "Coding job started", workdir=str(resolved_workdir))
    emit({"job_id": job_id, "workdir": str(resolved_workdir)})


def status(job_id):
    p = paths(job_id)
    if not p["status"].exists():
        emit({"error": "no such job"})
        return
    emit(refresh_status(job_id))


def refresh_status(job_id):
    """Detect a dead or stalled detached worker so callers never wait forever."""
    p = paths(job_id)
    state = json.loads(p["status"].read_text())
    if state.get("status") != "running":
        return state

    # `spawn` writes the PID immediately after starting the detached process.
    # Until it is available, preserve the running state rather than treating a
    # short startup race (or a journal-only consumer) as a failed worker.
    if not p["pid"].exists():
        return state
    pid = None
    try:
        pid = int(p["pid"].read_text().strip())
        os.kill(pid, 0)
        # A zombie still answers kill(pid, 0), but cannot update status.
        proc_stat = Path(f"/proc/{pid}/stat")
        if proc_stat.exists() and proc_stat.read_text().split()[2] == "Z":
            pid = None
    except (ValueError, OSError):
        pid = None
    if pid is None:
        reason = "worker_exited"
        message = "Agent worker exited without recording a final status"
    elif time.time() - state.get("updated_at", time.time()) > JOB_IDLE_TIMEOUT_SECONDS:
        reason = "stalled_timeout"
        message = f"Agent made no progress for {JOB_IDLE_TIMEOUT_SECONDS}s; stopping the stalled worker"
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass
    else:
        return state

    write_status(job_id, status="failed", reason=reason, error=message)
    record_event(job_id, "failed", message)
    return json.loads(p["status"].read_text())


def log(job_id, after=0):
    p = paths(job_id)
    if not p["status"].exists():
        emit({"error": "no such job"})
        return
    try:
        after = max(0, int(after))
    except (TypeError, ValueError):
        emit({"error": "after must be a non-negative cursor"})
        return
    state = refresh_status(job_id)
    events = []
    if p["events"].exists():
        for line in p["events"].read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("cursor", 0) > after:
                events.append(event)
    emit({"job_id": job_id, "status": state.get("status"),
          "reason": state.get("reason"), "error": state.get("error"),
          "workdir": state.get("workdir"),
          "task": state.get("task"), "events": events,
          "cursor": after, "next_cursor": state.get("event_cursor", after)})


def send(job_id, message):
    p = paths(job_id)
    if not p["input_queue"].exists():
        emit({"error": "no such job"})
        return
    (p["input_queue"] / f"{time.time_ns()}.txt").write_text(message)
    emit({"queued": True})


def stop(job_id):
    p = paths(job_id)
    if not p["pid"].exists():
        emit({"error": "no such job"})
        return
    try:
        os.killpg(os.getpgid(int(p["pid"].read_text())), signal.SIGTERM)
    except ProcessLookupError:
        pass
    write_status(job_id, status="stopped", reason="user_stop")
    record_event(job_id, "stopped", "Coding job stopped")
    emit({"stopped": True})


def queued_messages(p):
    messages = []
    for message_file in sorted(p["input_queue"].glob("*.txt")):
        messages.append(message_file.read_text())
        message_file.unlink()
    return messages


def bound_plain_cat(command):
    """Prevent common accidental full-file dumps without changing shell IO."""
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
    paths = " ".join(shlex.quote(part) for part in parts[1:])
    return f"head -n {MAX_FILE_PREVIEW_LINES} -- {paths}"


def worker(job_id):
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    p = paths(job_id)
    state = json.loads(p["status"].read_text())
    workdir = Path(state["workdir"])
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    def declaration(name, description, properties, required):
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=types.Schema(type=types.Type.OBJECT, properties=properties, required=required),
        )

    string = lambda description: types.Schema(type=types.Type.STRING, description=description)
    boolean = lambda description: types.Schema(type=types.Type.BOOLEAN, description=description)
    agent_tools = types.Tool(function_declarations=[
        declaration("bash", "Run a bash command in the project workdir. Use it for search, files, tests, and verification. Never dump files with cat: preview at most 16 lines using head -n 16 or sed -n 'START,ENDp'.",
                    {"command": string("Command to execute."), "timeout_s": types.Schema(type=types.Type.INTEGER, description="Optional timeout in seconds.")}, ["command"]),
        declaration("Read", "Read a UTF-8 text file relative to the project workdir.", {"path": string("File path.")}, ["path"]),
        declaration("Edit", "Make a precise replacement in a UTF-8 text file. Use bash for new files or broader generated output.",
                    {"path": string("File path."), "old_string": string("Exact existing text."), "new_string": string("Replacement text."), "replace_all": boolean("Replace every occurrence.")}, ["path", "old_string", "new_string"]),
        declaration("narrate", "Report a short user-facing milestone to Priya. Call before investigation, before edits, and after verification.",
                    {"message": string("Concise progress update with what you are doing or learned.")}, ["message"]),
    ])
    system = (
        "You are an autonomous coding agent working for Priya. Work in "
        f"{workdir}. You have bash, Read, Edit, and narrate. Call narrate with "
        "a concise milestone before investigating, before making changes, and after "
        "verification; Priya relays those updates live to the user. Use bash output "
        "and tests as evidence. Use the shell inspection toolkit deliberately: prefer "
        "`rg --files`, `rg -n`, `rg -l`, and `rg -g` for discovery/search; use `grep -n`, "
        "`grep -R`, or `find` if rg is unavailable, and `fd` when installed. Use `git "
        "status`, `git diff`, `git log`, and `git show` for repository facts; `ls -la`, "
        "`stat`, `file`, `du -sh`, and `wc -l` for filesystem facts; `diff -u` or `cmp` "
        "for comparisons; and `sed`, `awk`, `cut`, `sort`, `uniq`, `tr`, `jq`, and `yq` "
        "for extraction. Use `xargs` only with safely delimited input (`-print0` / `-0`) "
        "and `tee` only for intentional writes. For file inspection, never use cat or "
        "dump a whole file. Preview only 16 lines at a time with `head -n 16 PATH`, "
        "`tail -n 16 PATH`, or `sed -n 'START,ENDp' PATH`; locate relevant symbols first, "
        "then page through large files in narrow ranges. Do not use `cat`, `less`, or "
        "`more` for source inspection. Complete the task, verify it, then say DONE."
    )
    contents = [types.Content(role="user", parts=[types.Part(text=state["task"])])]

    def run_shell(command, timeout=SHELL_TIMEOUT_SECONDS):
        """Bash equivalent with incremental output recorded to the event journal."""
        try:
            command = bound_plain_cat(command)
            record_event(job_id, "command", f"$ {command}")
            proc = subprocess.Popen(command, shell=True, cwd=workdir, text=True, bufsize=1,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            pending = queue.Queue()
            def drain(stream_name, stream):
                for line in iter(stream.readline, ""):
                    pending.put((stream_name, line))
                pending.put((stream_name, None))
            for stream_name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                threading.Thread(target=drain, args=(stream_name, stream), daemon=True).start()
            captured, closed, deadline = {"stdout": "", "stderr": ""}, 0, time.monotonic() + timeout
            while closed < 2:
                if time.monotonic() >= deadline and proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    record_event(job_id, "command_error", f"Command timed out after {timeout}s")
                try:
                    stream_name, line = pending.get(timeout=.1)
                except queue.Empty:
                    continue
                if line is None:
                    closed += 1
                    continue
                captured[stream_name] = (captured[stream_name] + line)[-8000:]
                record_event(job_id, "command_output", line.rstrip("\r\n"), stream=stream_name)
            code = proc.wait()
            record_event(job_id, "command_result", f"Command exited {code}", exit_code=code)
            return f"[exit {code}]\n{captured['stdout']}{captured['stderr']}"
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT after {SHELL_TIMEOUT_SECONDS}s]"
        except Exception as exc:
            return f"[ERROR: {exc}]"

    try:
        for turn in range(1, MAX_TURNS + 1):
            write_status(job_id, turn=turn)
            record_event(job_id, "turn", f"Agent planning turn {turn}", turn=turn)
            for message in queued_messages(p):
                print(f"\n[STEERING RECEIVED] {message}", flush=True)
                contents.append(types.Content(role="user", parts=[types.Part(text=f"[steering] {message}")]))
            for retry in range(MAX_API_RETRIES + 1):
                try:
                    stream = client.models.generate_content_stream(
                        model=MODEL, contents=contents,
                        config=types.GenerateContentConfig(system_instruction=system, tools=[agent_tools]),
                    )
                    text, calls, model_parts = [], [], []
                    for chunk in stream:
                        if not chunk.candidates:
                            continue
                        for part in chunk.candidates[0].content.parts:
                            if part.text:
                                text.append(part.text)
                            if part.function_call:
                                calls.append(part.function_call)
                            model_parts.append(part)
                    break
                except genai_errors.ClientError as exc:
                    if retry == MAX_API_RETRIES:
                        write_status(job_id, status="failed", reason="api_error", error=str(exc), api_retries=retry)
                        record_event(job_id, "failed", f"Agent API failed after {MAX_API_RETRIES} retries: {exc}")
                        return
                    retry_hint = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", str(exc), re.IGNORECASE)
                    delay = min(60, float(retry_hint.group(1))) if retry_hint else 2 ** (retry + 1)
                    write_status(job_id, api_retries=retry + 1, last_api_error=str(exc))
                    record_event(job_id, "retry", f"Agent API failed; retrying ({retry + 1}/{MAX_API_RETRIES}) in {delay}s")
                    time.sleep(max(1, delay))

            contents.append(types.Content(role="model", parts=model_parts))
            response_text = "".join(text)
            print(f"\n=== TURN {turn} ===\n{response_text}", flush=True)
            if response_text.strip():
                record_event(job_id, "agent_message", response_text.strip()[-2000:])
            if not calls:
                if "DONE" in response_text.upper():
                    write_status(job_id, status="done", reason=None)
                    record_event(job_id, "done", "Agent reported completion")
                    return
                contents.append(types.Content(role="user", parts=[types.Part(text="Continue or say DONE.")]))
                continue

            responses = []
            for call in calls:
                args = dict(call.args)
                if call.name == "narrate":
                    message = args.get("message", "").strip()
                    output = "Recorded" if message else "Narration requires a message"
                    if message:
                        record_event(job_id, "narration", message)
                elif call.name == "bash":
                    output = run_shell(args.get("command", ""), args.get("timeout_s", SHELL_TIMEOUT_SECONDS))
                elif call.name == "Read":
                    try:
                        output = (workdir / args.get("path", "")).read_text(encoding="utf-8")[-12000:]
                        record_event(job_id, "read", f"Read {args.get('path')}")
                    except Exception as exc:
                        output = f"[ERROR: {exc}]"
                elif call.name == "Edit":
                    try:
                        path = workdir / args.get("path", "")
                        source = path.read_text(encoding="utf-8")
                        old, new = args.get("old_string", ""), args.get("new_string", "")
                        count = source.count(old)
                        if not old or not count:
                            output = "[ERROR: old_string was not found]"
                        elif count > 1 and not args.get("replace_all", False):
                            output = f"[ERROR: old_string occurs {count} times; use more context or replace_all]"
                        else:
                            path.write_text(source.replace(old, new, -1 if args.get("replace_all") else 1), encoding="utf-8")
                            output = f"Edited {path} ({count if args.get('replace_all') else 1} replacement)"
                            record_event(job_id, "edit", output, path=str(path))
                    except Exception as exc:
                        output = f"[ERROR: {exc}]"
                else:
                    output = f"[ERROR: unknown tool {call.name}]"
                print(output, flush=True)
                responses.append(types.Part(function_response=types.FunctionResponse(
                    name=call.name, response={"output": output},
                )))
            contents.append(types.Content(role="user", parts=responses))
        write_status(job_id, status="failed", reason="max_turns_exceeded")
        record_event(job_id, "failed", "Agent exceeded its maximum number of turns")
    except Exception as exc:
        write_status(job_id, status="failed", reason="crash", error=str(exc))
        record_event(job_id, "failed", f"Agent crashed: {exc}")
        raise


def usage():
    print("Usage: job_runner.py {spawn|status|log|send|stop} ...", file=sys.stderr)


def main():
    args = sys.argv[1:]
    if not args:
        usage()
        return 2
    command = args[0]
    if command == "spawn" and len(args) in (2, 3):
        spawn(args[1], args[2] if len(args) == 3 else None)
    elif command == "status" and len(args) == 2:
        status(args[1])
    elif command == "log" and len(args) in (2, 3):
        log(args[1], args[2] if len(args) == 3 else 0)
    elif command == "send" and len(args) == 3:
        send(args[1], args[2])
    elif command == "stop" and len(args) == 2:
        stop(args[1])
    elif command == "_worker" and len(args) == 2:
        worker(args[1])
    else:
        usage()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
