"""
Priya — Gemini Live backend.

Driven by priya.sh (shell UI). Pure line-based I/O:
  stdin  -> one line = one user message
  stdout -> one line = one full assistant reply (flushed immediately)
  stderr -> connection/status noise only, ignored by the shell

--talk: play the model's audio output through aplay as it streams
in. Silent (text-only) without the flag.
"""

import os
import sys
import json
import asyncio
import traceback
import subprocess

from google import genai
from google.genai import types

MODEL = "gemini-3.8-live-extended-thinking"
TALK = "--talk" in sys.argv[1:]

client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key=os.environ.get("GEMINI_API_KEY"),
)

AGENTJOB_BIN = os.path.expanduser("~/agent/job_runner.py")

agentjob_declaration = types.FunctionDeclaration(
    name="agentjob",
    behavior="NON_BLOCKING",
    description=(
        "Manage a background coding subagent (Gemini 3.5 Flash Lite with shell access). "
        "Use 'spawn' to delegate a self-contained build/fix task - it runs in the "
        "background, non-blocking. Use 'status' to poll progress (never blocks). "
        "Use 'log' to read its transcript. Use 'send' to steer it (applies at its next "
        "turn boundary, not mid-generation). Use 'stop' to hard-kill it; partial files "
        "remain in its workdir for you to inspect or finish yourself."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["spawn", "status", "log", "send", "stop"]},
            "task": {"type": "STRING", "description": "Required for 'spawn'."},
            "workdir": {"type": "STRING", "description": "Optional for 'spawn'."},
            "job_id": {"type": "STRING", "description": "Required for status/log/send/stop."},
            "message": {"type": "STRING", "description": "Required for 'send'."},
        },
        "required": ["action"],
    },
)


def run_agentjob(args: dict) -> dict:
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
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_output": raw, "stderr": result.stderr[-500:]}
    except subprocess.TimeoutExpired:
        return {"error": "agentjob call itself timed out (30s)"}
    except Exception as e:
        return {"error": str(e)}


bash_declaration = types.FunctionDeclaration(
    name="bash",
    behavior="NON_BLOCKING",
    description=(
        "Run a bash command directly and return its output. Use this to read/write "
        "files, inspect a subagent's workdir, verify a subagent's claims, check "
        "processes, or do anything else yourself. Runs with your current user's "
        "permissions, no sandboxing."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "command": {"type": "STRING", "description": "The bash command to run."},
            "timeout_s": {"type": "INTEGER", "description": "Optional. Default 60."},
        },
        "required": ["command"],
    },
)


def run_bash(args: dict) -> dict:
    command = args.get("command")
    if not command:
        return {"error": "bash requires 'command'"}
    timeout_s = args.get("timeout_s", 60)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout_s,
        )
        return {
            "exit_code": result.returncode,
            "stdout": (result.stdout or "")[-8000:],
            "stderr": (result.stderr or "")[-4000:],
        }
    except subprocess.TimeoutExpired:
        return {"error": f"command timed out after {timeout_s}s"}
    except Exception as e:
        return {"error": str(e)}


CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    output_audio_transcription=types.AudioTranscriptionConfig(),
    system_instruction=(
        "You are Priya. For ANY request that touches files, commands, code, processes, or system state, you MUST call the 'bash' or 'agentjob' tool before responding -- never answer from assumption, and never claim an error occurred unless a tool call actually returned one. You have a 'bash' tool for direct shell "
        "access, and an 'agentjob' tool to delegate self-contained coding tasks to a "
        "background subagent. Prefer spawning agentjob for substantial builds so you "
        "can keep talking with the user; use bash directly for quick checks, reading "
        "files, or verifying a subagent's work. Never trust a subagent's 'done' claim "
        "without checking its log or output yourself. If a tool call fails or errors, "
        "state the actual error message returned by the tool -- never say a generic "
        "'system error occurred'."
    ),
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name="Kore",
            )
        ),
        language_code="en-IN",
    ),
    thinking_config=types.ThinkingConfig(thinking_level="medium"),
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=120000,
        sliding_window=types.SlidingWindow(target_tokens=60000),
    ),
    tools=[types.Tool(function_declarations=[agentjob_declaration, bash_declaration])],
)

PIPED = not sys.stdin.isatty()


def out(line: str):
    if PIPED:
        sys.stdout.write(line.replace("\n", " ") + "\n")
        sys.stdout.flush()
    else:
        print(line)


def start_player():
    return subprocess.Popen(
        ["aplay", "-q", "-f", "S16_LE", "-r", "24000", "-c", "1", "-t", "raw"],
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def emit_content(sc):
    if sc is None:
        return

    emitted_any = False

    if sc.output_transcription and sc.output_transcription.text:
        out(sc.output_transcription.text)
        emitted_any = True

    if sc.model_turn and sc.model_turn.parts:
        for part in sc.model_turn.parts:
            text = getattr(part, "text", None)
            if text:
                out(text)
                emitted_any = True

    return emitted_any


class TextLoop:
    def __init__(self):
        self.session = None
        self.player = start_player() if TALK else None

    async def send_text(self):
        while True:
            if PIPED:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                text = line.rstrip("\n")
            else:
                text = await asyncio.to_thread(input, "message > ")

            if text.lower() == "q":
                break
            if self.session is not None:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": text or "."}]},
                    turn_complete=True,
                )

    async def receive_text(self):
        while True:
            if self.session is not None:
                idle = False
                while not idle:
                    turn = self.session.receive()
                    try:
                        while True:
                            response = await asyncio.wait_for(turn.__anext__(), timeout=180)
                            sc = response.server_content
                            print(f"RAW: {response}", file=sys.stderr)

                            if response.tool_call:
                                function_responses = []
                                for fc in response.tool_call.function_calls:
                                    args_dict = dict(fc.args)
                                    detail = args_dict.get("command") or args_dict.get("task") or json.dumps(args_dict)
                                    out("<<TOOL_START>>" + json.dumps({"name": fc.name, "detail": detail}))
                                    if fc.name == "agentjob":
                                        result = run_agentjob(args_dict)
                                    elif fc.name == "bash":
                                        result = run_bash(args_dict)
                                    else:
                                        result = {"error": f"unknown tool {fc.name}"}
                                    print(f"TOOL CALL: {fc.name} {args_dict} -> {result}", file=sys.stderr)
                                    out("<<TOOL_END>>" + json.dumps({"name": fc.name, "result": result}))
                                    function_responses.append(
                                        types.FunctionResponse(
                                            id=fc.id, name=fc.name, response=result,
                                        )
                                    )
                                await self.session.send_tool_response(
                                    function_responses=function_responses
                                )

                            emit_content(sc)

                            if TALK and response.data:
                                self.player.stdin.write(response.data)
                                self.player.stdin.flush()

                            status = getattr(sc, "interaction_status", None) if sc is not None else None
                            if status == "IDLE":
                                idle = True
                    except StopAsyncIteration:
                        # The turn's generator ended on its own — this IS
                        # end-of-turn even when no interaction_status ever
                        # arrived. Previously this was `pass`, which left
                        # idle False and looped back into a second
                        # self.session.receive() that then blocked up to
                        # 90s waiting for a turn that was already over,
                        # so <<END>> never fired and the UI stayed "busy"
                        # forever after ordinary replies like "Pong!".
                        idle = True
                    except asyncio.TimeoutError:
                        print("receive_text: stalled turn, forcing end", file=sys.stderr)
                        idle = True
                out("<<END>>")

    async def run(self):
        try:
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                await asyncio.sleep(0.1)
                send_text_task = tg.create_task(self.send_text())
                tg.create_task(self.receive_text())
                await send_text_task
                raise asyncio.CancelledError("User requested exit")
        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            if self.player is not None:
                self.player.stdin.close()
                self.player.wait()


if __name__ == "__main__":
    main = TextLoop()
    asyncio.run(main.run())
