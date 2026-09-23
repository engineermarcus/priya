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
import queue
import threading
import time
from collections import deque

from google import genai
from google.genai import types

MODEL = "gemini-3.8-live"
TALK = "--talk" in sys.argv[1:]
MIC = "--mic" in sys.argv[1:]

if MIC:
    import pyaudio
    import webrtcvad
    import numpy as np
    from pywebrtc_audio import AudioProcessor
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    SEND_SAMPLE_RATE = 16000
    # WebRTC VAD accepts 10, 20, or 30 ms frames. Thirty milliseconds gives
    # low latency without treating tiny room sounds as a complete utterance.
    CHUNK_SIZE = 480
    pya = pyaudio.PyAudio()

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


def run_bash(args: dict, on_output=None) -> dict:
    """Run a shell command and forward its stdout/stderr as it arrives."""
    command = args.get("command")
    if not command:
        return {"error": "bash requires 'command'"}
    timeout_s = args.get("timeout_s", 60)
    try:
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
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
        deadline = time.monotonic() + timeout_s
        while closed_streams < 2:
            if not timed_out and time.monotonic() >= deadline:
                proc.kill()
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
        if timed_out:
            return {"error": f"command timed out after {timeout_s}s"}
        return {
            "exit_code": returncode,
            "stdout": captured["stdout"],
            "stderr": captured["stderr"],
        }
    except Exception as e:
        return {"error": str(e)}


CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    # Input transcription makes spoken turns available to the TUI as normal
    # user messages. Output remains audio, with its transcription streamed so
    # text and tool activity are rendered alongside playback.
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    # Keep Gemini's automatic VAD as the authoritative fallback. The client
    # also detects speech and sends audio_stream_end for faster finalization.
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=120,
            silence_duration_ms=450,
        ),
        # Permit a real user to barge in. Echo filtering below rejects Priya's
        # own playback before it reaches this server-side interruption path.
        activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
    ),
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
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=120000,
        sliding_window=types.SlidingWindow(target_tokens=60000),
    ),
    tools=[types.Tool(function_declarations=[agentjob_declaration, bash_declaration])],
)

PIPED = not sys.stdin.isatty()
OUT_LOCK = threading.Lock()


def out(line: str):
    # Shell output is read on helper threads while the Live receiver also
    # writes protocol events. Keep every protocol message on one stdout line.
    with OUT_LOCK:
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


def emit_input_transcription(sc):
    """Send finalized microphone speech to the frontend as a user turn."""
    if sc is None:
        return
    transcription = getattr(sc, "input_transcription", None)
    text = getattr(transcription, "text", None)
    if text:
        out("<<USER_SPEECH>>" + text)


class MicrophoneProcessor:
    """WebRTC AEC/NS and a local end-of-speech signal for Gemini hybrid VAD."""

    def __init__(self):
        self.vad = webrtcvad.Vad(3)
        self.processor = AudioProcessor(
            sample_rate=SEND_SAMPLE_RATE,
            echo_cancellation=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=False,
            ns_level=2,
            stream_delay_ms=60,
        )
        self.far_24khz = deque()
        self.far_16khz = deque()
        self.speech_active = False
        self.silent_frames = 0

    def reset(self):
        self.processor.reset()
        self.far_24khz.clear()
        self.far_16khz.clear()
        self.speech_active = False
        self.silent_frames = 0

    def add_playback(self, pcm_24khz):
        """Add the PCM sent to aplay as the far-end AEC reference signal."""
        self.far_24khz.extend(memoryview(pcm_24khz).cast("h"))
        # Convert 24 kHz playback to 16 kHz for the microphone processor.
        while len(self.far_24khz) >= 3:
            first = self.far_24khz.popleft()
            second = self.far_24khz.popleft()
            third = self.far_24khz.popleft()
            self.far_16khz.append(first)
            self.far_16khz.append((second + third) // 2)

    def process(self, microphone_pcm):
        """Return cleaned PCM and whether local VAD finalized an utterance."""
        near = np.frombuffer(microphone_pcm, dtype="<i2")
        if len(self.far_16khz) >= len(near):
            far = np.fromiter(
                (self.far_16khz.popleft() for _ in range(len(near))),
                dtype=np.int16,
                count=len(near),
            )
        else:
            far = np.zeros(len(near), dtype=np.int16)
        cleaned = self.processor.process(near, far)
        cleaned_pcm = cleaned.astype("<i2", copy=False).tobytes()
        voice = self.vad.is_speech(cleaned_pcm, SEND_SAMPLE_RATE)

        if voice:
            self.speech_active = True
            self.silent_frames = 0
            return cleaned_pcm, False
        if not self.speech_active:
            return cleaned_pcm, False
        self.silent_frames += 1
        if self.silent_frames < 15:  # 450 ms of silence
            return cleaned_pcm, False
        self.speech_active = False
        self.silent_frames = 0
        return cleaned_pcm, True


class TextLoop:
    def __init__(self):
        self.session = None
        self.player = start_player() if TALK else None
        self.mic_queue = asyncio.Queue(maxsize=10) if MIC else None
        self.mic_stream = None
        self._tool_event_id = 0
        self.mic_processor = MicrophoneProcessor() if MIC else None

    def discard_playback(self):
        """Immediately drop audio queued in aplay after a server interruption."""
        if self.player is None:
            return
        old_player = self.player
        self.player = None
        try:
            old_player.stdin.close()
        except (BrokenPipeError, OSError, AttributeError):
            pass
        old_player.terminate()
        try:
            old_player.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            old_player.kill()
        if TALK:
            self.player = start_player()

    async def listen_audio(self):
        """Reads mic PCM chunks off-thread and queues them for send_audio."""
        mic_info = pya.get_default_input_device_info()
        self.mic_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )
        while True:
            data = await asyncio.to_thread(
                self.mic_stream.read, CHUNK_SIZE, exception_on_overflow=False
            )
            await self.mic_queue.put(data)

    async def send_audio(self):
        """Use local noise/VAD gating with Gemini's automatic VAD as fallback."""
        while True:
            data = await self.mic_queue.get()
            cleaned_audio, speech_ended = self.mic_processor.process(data)
            if self.session is not None:
                await self.session.send_realtime_input(
                    audio={"data": cleaned_audio, "mime_type": "audio/pcm;rate=16000"}
                )
                if speech_ended:
                    await self.session.send_realtime_input(audio_stream_end=True)

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
                # A function-call stream ends immediately after its responses
                # are sent. Gemini then opens a new stream for its answer. Do
                # not end the frontend turn at that handoff or its audio
                # transcription will have no assistant bubble to update.
                saw_tool_call = False
                reached_idle = False
                timed_out = False
                turn = self.session.receive()
                try:
                    while True:
                        response = await asyncio.wait_for(turn.__anext__(), timeout=180)
                        sc = response.server_content
                        print(f"RAW: {response}", file=sys.stderr)

                        emit_input_transcription(sc)

                        was_interrupted = bool(sc is not None and sc.interrupted)
                        if was_interrupted:
                            # Gemini documents that client playback must be
                            # cleared here. Without this, queued speech keeps
                            # reaching the microphone after the turn ends.
                            self.discard_playback()
                            if self.mic_processor is not None:
                                self.mic_processor.reset()

                        if response.tool_call:
                            saw_tool_call = True
                            function_responses = []
                            for fc in response.tool_call.function_calls:
                                self._tool_event_id += 1
                                tool_event_id = self._tool_event_id
                                args_dict = dict(fc.args)
                                detail = args_dict.get("command") or args_dict.get("task") or json.dumps(args_dict)
                                out("<<TOOL_START>>" + json.dumps({
                                    "id": tool_event_id, "name": fc.name, "detail": detail,
                                }))

                                def emit_tool_output(stream_name, text, event_id=tool_event_id):
                                    if text:
                                        out("<<TOOL_LOG>>" + json.dumps({
                                            "id": event_id, "stream": stream_name, "text": text,
                                        }))

                                if fc.name == "agentjob":
                                    result = await asyncio.to_thread(run_agentjob, args_dict)
                                elif fc.name == "bash":
                                    result = await asyncio.to_thread(run_bash, args_dict, emit_tool_output)
                                else:
                                    result = {"error": f"unknown tool {fc.name}"}
                                print(f"TOOL CALL: {fc.name} {args_dict} -> {result}", file=sys.stderr)
                                out("<<TOOL_END>>" + json.dumps({
                                    "id": tool_event_id, "name": fc.name, "result": result,
                                }))
                                function_responses.append(
                                    types.FunctionResponse(
                                        id=fc.id, name=fc.name, response=result,
                                    )
                                )
                            await self.session.send_tool_response(
                                function_responses=function_responses
                            )

                        emit_content(sc)

                        if TALK and response.data and self.player is not None and not was_interrupted:
                            if self.mic_processor is not None:
                                self.mic_processor.add_playback(response.data)
                            self.player.stdin.write(response.data)
                            self.player.stdin.flush()

                        status = getattr(sc, "interaction_status", None) if sc is not None else None
                        if status == "IDLE":
                            reached_idle = True
                            break
                except StopAsyncIteration:
                    pass
                except asyncio.TimeoutError:
                    print("receive_text: stalled turn, forcing end", file=sys.stderr)
                    timed_out = True

                # A stream that carried tool calls is only the tool round. The
                # next receive() stream carries the model's spoken/text answer.
                if reached_idle or timed_out or not saw_tool_call:
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
                if MIC:
                    tg.create_task(self.listen_audio())
                    tg.create_task(self.send_audio())
                await send_text_task
                raise asyncio.CancelledError("User requested exit")
        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            if self.player is not None:
                try:
                    self.player.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                self.player.wait()


if __name__ == "__main__":
    main = TextLoop()
    asyncio.run(main.run())
