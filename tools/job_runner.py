#!/usr/bin/env python3
"""Repository-local background runner used by Priya's ``agentjob`` tool.

Jobs, logs, and steering messages are kept below ``.priya/jobs`` so a cloned
repository contains every executable component. Runtime job data is ignored by
Git; the runner itself is versioned in ``tools/``.
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = Path(os.environ.get("PRIYA_JOBS_DIR", PROJECT_ROOT / ".priya" / "jobs"))
PYTHON = sys.executable
MODEL = "gemini-3.5-flash-lite"
MAX_TURNS = 60
SHELL_TIMEOUT_SECONDS = 120


def paths(job_id):
    base = JOBS_DIR / job_id
    return {
        "base": base,
        "status": base / "status.json",
        "log": base / "log.txt",
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
        workdir=str(resolved_workdir), created_at=time.time(),
    )
    p["log"].write_text("")
    env = os.environ.copy()
    env["PRIYA_AGENT_JOB_ID"] = job_id
    with open(p["log"], "a") as log:
        proc = subprocess.Popen(
            [PYTHON, str(Path(__file__).resolve()), "_worker", job_id],
            stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True,
        )
    p["pid"].write_text(str(proc.pid))
    emit({"job_id": job_id, "workdir": str(resolved_workdir)})


def status(job_id):
    p = paths(job_id)
    if not p["status"].exists():
        emit({"error": "no such job"})
        return
    emit(json.loads(p["status"].read_text()))


def log(job_id):
    p = paths(job_id)
    if not p["log"].exists():
        emit({"error": "no such job"})
        return
    print(p["log"].read_text(), end="")


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
    emit({"stopped": True})


def queued_messages(p):
    messages = []
    for message_file in sorted(p["input_queue"].glob("*.txt")):
        messages.append(message_file.read_text())
        message_file.unlink()
    return messages


def worker(job_id):
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    p = paths(job_id)
    state = json.loads(p["status"].read_text())
    workdir = Path(state["workdir"])
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    shell_tool = types.Tool(function_declarations=[types.FunctionDeclaration(
        name="shell", description="Run a bash command in the project workdir.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"command": types.Schema(type=types.Type.STRING)},
            required=["command"],
        ),
    )])
    system = (
        "You are an autonomous coding agent with shell access. Work in "
        f"{workdir}. Complete the task, verify it, then say DONE."
    )
    contents = [types.Content(role="user", parts=[types.Part(text=state["task"])])]

    def run_shell(command):
        try:
            result = subprocess.run(
                command, shell=True, cwd=workdir, capture_output=True, text=True,
                timeout=SHELL_TIMEOUT_SECONDS,
            )
            return f"[exit {result.returncode}]\n{((result.stdout or '') + (result.stderr or ''))[-8000:]}"
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT after {SHELL_TIMEOUT_SECONDS}s]"
        except Exception as exc:
            return f"[ERROR: {exc}]"

    try:
        for turn in range(1, MAX_TURNS + 1):
            write_status(job_id, turn=turn)
            for message in queued_messages(p):
                print(f"\n[STEERING RECEIVED] {message}", flush=True)
                contents.append(types.Content(role="user", parts=[types.Part(text=f"[steering] {message}")]))
            try:
                stream = client.models.generate_content_stream(
                    model=MODEL, contents=contents,
                    config=types.GenerateContentConfig(system_instruction=system, tools=[shell_tool]),
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
            except genai_errors.ClientError as exc:
                write_status(job_id, status="failed", reason="api_error", error=str(exc))
                return

            contents.append(types.Content(role="model", parts=model_parts))
            response_text = "".join(text)
            print(f"\n=== TURN {turn} ===\n{response_text}", flush=True)
            if not calls:
                if "DONE" in response_text.upper():
                    write_status(job_id, status="done", reason=None)
                    return
                contents.append(types.Content(role="user", parts=[types.Part(text="Continue or say DONE.")]))
                continue

            responses = []
            for call in calls:
                command = call.args.get("command", "")
                print(f"$ {command}", flush=True)
                output = run_shell(command)
                print(output, flush=True)
                responses.append(types.Part(function_response=types.FunctionResponse(
                    name="shell", response={"output": output},
                )))
            contents.append(types.Content(role="user", parts=responses))
        write_status(job_id, status="failed", reason="max_turns_exceeded")
    except Exception as exc:
        write_status(job_id, status="failed", reason="crash", error=str(exc))
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
    elif command == "log" and len(args) == 2:
        log(args[1])
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
