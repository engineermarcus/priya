"""
agent.py — Agent tool backed by Gemini CLI, async/background execution.

Lifecycle (mirrors spawn_agent / wait_agent / close_agent):
    job_id = spawn_agent(instructions, files=[...])
    status = check_agent(job_id)          # non-blocking poll
    result = wait_agent(job_id)           # blocks until done
    close_agent(job_id)                   # kill if still running

Each agent call runs `gemini -p <instructions> @file1 @file2 ...`
as a background subprocess. Output is captured to disk so it survives
even if the calling process restarts.
"""

import subprocess
import uuid
import json
import os
import signal
import time
from pathlib import Path

JOBS_DIR = Path(os.environ.get("AGENT_JOBS_DIR", "/tmp/agent_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _job_paths(job_id: str):
    job_dir = JOBS_DIR / job_id
    return {
        "dir": job_dir,
        "stdout": job_dir / "stdout.log",
        "stderr": job_dir / "stderr.log",
        "meta": job_dir / "meta.json",
    }


DEFAULT_MODEL = "gemini-3.5-flash"


def spawn_agent(instructions: str, files: list[str] = None, model: str = None) -> str:
    """
    Launches Gemini CLI in the background. Returns a job_id immediately.
    Does NOT block waiting for completion.
    """
    files = files or []
    job_id = str(uuid.uuid4())[:8]
    paths = _job_paths(job_id)
    paths["dir"].mkdir(parents=True, exist_ok=True)

    model = model or DEFAULT_MODEL
    cmd = ["gemini", "-p", instructions, "--output-format", "stream-json", "-m", model]

    # Gemini CLI rejects @file as a positional arg alongside -p.
    # Feed file contents via stdin instead (per -p's documented stdin append behavior).
    stdin_data = ""
    for f in files:
        try:
            content = Path(f).read_text()
            stdin_data += f"\n--- {f} ---\n{content}\n"
        except OSError as e:
            stdin_data += f"\n--- {f} (could not read: {e}) ---\n"

    stdout_f = open(paths["stdout"], "w")
    stderr_f = open(paths["stderr"], "w")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_data else None,
        stdout=stdout_f,
        stderr=stderr_f,
        start_new_session=True,  # detach so it survives parent exit
        text=True,
    )
    if stdin_data:
        proc.stdin.write(stdin_data)
        proc.stdin.close()

    meta = {
        "job_id": job_id,
        "pid": proc.pid,
        "instructions": instructions,
        "files": files,
        "model": model,
        "status": "running",
        "started_at": time.time(),
    }
    paths["meta"].write_text(json.dumps(meta, indent=2))

    return job_id


def check_agent(job_id: str) -> dict:
    """
    Non-blocking poll. Returns current status without waiting.
    status: "running" | "completed" | "failed" | "not_found"
    """
    paths = _job_paths(job_id)
    if not paths["meta"].exists():
        return {"job_id": job_id, "status": "not_found"}

    meta = json.loads(paths["meta"].read_text())
    pid = meta["pid"]

    # os.kill(pid, 0) cannot tell a zombie from a truly running process —
    # a finished child stays "alive" to os.kill until reaped, which made
    # stream_agent/wait_agent poll forever after the job was actually done.
    # waitpid(WNOHANG) actually reaps it and tells us the real state.
    alive = True
    try:
        reaped_pid, _exit_status = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            alive = False  # just reaped it — it had finished
    except ChildProcessError:
        # not our child (already reaped elsewhere) or truly gone
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False

    if alive:
        meta["status"] = "running"
    else:
        # process ended — determine success/failure from exit info if we have it
        meta["status"] = meta.get("final_status", "completed")

    return meta


def wait_agent(job_id: str, timeout: int = 300, poll_interval: float = 2.0) -> dict:
    """
    Blocks until the agent finishes or timeout (seconds) is reached.
    Returns final result: {status, output, error}
    """
    paths = _job_paths(job_id)
    start = time.time()

    while time.time() - start < timeout:
        status = check_agent(job_id)
        if status["status"] != "running":
            break
        time.sleep(poll_interval)
    else:
        return {"job_id": job_id, "status": "timeout", "output": "", "error": "wait_agent timed out"}

    stdout = paths["stdout"].read_text() if paths["stdout"].exists() else ""
    stderr = paths["stderr"].read_text() if paths["stderr"].exists() else ""

    final_status = "completed" if not stderr.strip() else "completed_with_warnings"

    meta = json.loads(paths["meta"].read_text())
    meta["status"] = final_status
    meta["final_status"] = final_status
    paths["meta"].write_text(json.dumps(meta, indent=2))

    return {
        "job_id": job_id,
        "status": final_status,
        "output": stdout,
        "error": stderr,
    }


def stream_agent(job_id: str, poll_interval: float = 0.5):
    """
    Generator: yields parsed JSON events in real time as the agent works,
    instead of waiting silently for the whole job to finish.

    Usage:
        for event in stream_agent(job_id):
            print(event)
    """
    paths = _job_paths(job_id)
    if not paths["stdout"].exists():
        yield {"error": "job not found"}
        return

    with open(paths["stdout"], "r") as f:
        while True:
            line = f.readline()
            if line:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"raw": line}
                continue

            # no new line yet — check if the process has ended
            status = check_agent(job_id)
            if status["status"] != "running":
                break
            time.sleep(poll_interval)


def close_agent(job_id: str) -> dict:
    """
    Terminates a running agent job. No-op if already finished.
    """
    paths = _job_paths(job_id)
    if not paths["meta"].exists():
        return {"job_id": job_id, "status": "not_found"}

    meta = json.loads(paths["meta"].read_text())
    pid = meta["pid"]

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        result_status = "closed"
    except OSError:
        result_status = "already_finished"

    meta["status"] = result_status
    meta["final_status"] = result_status
    paths["meta"].write_text(json.dumps(meta, indent=2))

    return {"job_id": job_id, "status": result_status}


if __name__ == "__main__":
    # quick manual test — streams events live instead of waiting silently
    jid = spawn_agent("Summarize this file", files=["agent.py"])
    print("spawned:", jid)
    for event in stream_agent(jid):
        print("event:", event)
    print("final:", check_agent(jid))
