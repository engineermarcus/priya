# Priya

Priya is a terminal interface for Gemini Live. It accepts typed and microphone
input, plays native audio responses, streams the corresponding text, and shows
every tool call and result in the conversation.

## Requirements

- Python 3.11 or newer
- A `GEMINI_API_KEY` environment variable
- ALSA's `aplay` command for model audio playback
- PortAudio development libraries when installing PyAudio from source

On Debian or Ubuntu, install the system audio packages with:

```sh
sudo apt install alsa-utils portaudio19-dev
```

## Setup

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY="your-key"
```

## Run

```sh
python priya.py
python priya.py --talk
python priya.py --mic
```

`--talk` plays the model's live audio response. `--mic` captures microphone
audio, displays the final speech transcription as a user message, and enables
model audio playback while retaining streamed text and tool results in the UI.
Microphone mode uses local WebRTC voice activity detection, drops background
noise, and mutes microphone frames captured during Priya's playback to prevent
speaker echo from becoming a new model turn. Headphones remain the most robust
audio setup.

Press `Ctrl+C` to exit. In the input field, enter `q` to quit.

## Tools

The model can call `bash` for local shell work and `agentjob` for non-trivial
front-end implementation in this repository, including pages, components,
layouts, styles, and browser interactions. `agentjob` runs in the background,
so Priya can continue a normal conversation while it works. Its agent narrates
milestones, and its shell commands/output stream into the same expandable live
tool log used by `bash`; neither requires a later `status` or `log` call.
Priya still inspects and verifies the changed files before reporting completion.
`agentjob` uses the repository-local runner at `tools/job_runner.py`. Its
transient job state and append-only event journal live under `.priya/jobs/`
(ignored by Git).

For a development server, watcher, or other long-running local command, Priya
can run `bash` in background mode. It immediately returns the process ID and
stdout/stderr log paths under `.priya/processes/`, so the conversation remains
available while the process runs.

Press Escape to interrupt the active response. Priya immediately shows
`Interrupted`, suppresses late streamed output, and kills any foreground shell
command it was running. Intentionally backgrounded processes continue until
they are stopped by their returned process-group PID.

Priya can also schedule session-scoped prompts through its cron tools. Schedules
use local-time numeric five-field cron expressions, wait for an idle model turn
before running, expire after three days, and disappear when Priya exits.

For surgical changes to existing text files, Priya uses `Read` followed by
`Edit`. Edit requires the file to be unchanged since its read, replaces an
exact old-text match (or every match when explicitly requested), displays a
unified diff, and waits for approval before writing.

The model can also publish a self-contained interactive HTML artifact. Artifact
revisions are saved under `.priya/artifacts/`, and its local server refreshes
an open artifact page automatically when a newer revision is published. Use
the `artifact share` tool action only when you want to expose that local server
through an installed `cloudflared` tunnel.

When a coding decision needs your input, the model can call `askUserQuestion`.
Priya pauses the tool round and renders its multiple-choice options in the
terminal. Choose with arrow keys and Enter, or press Tab and submit a custom
free-text answer; the model resumes with your answers.
