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

The model can call `bash` for local shell work and `agentjob` to delegate a
background coding task. Expand a running `bash` tool in the UI to follow its
stdout and stderr; the final result is appended after the live log. `agentjob`
uses the repository-local runner at `tools/job_runner.py`. Its transient job
state and logs live under `.priya/jobs/` (ignored by Git).

The model can also publish a self-contained interactive HTML artifact. Artifact
revisions are saved under `.priya/artifacts/`, and its local server refreshes
an open artifact page automatically when a newer revision is published. Use
the `artifact share` tool action only when you want to expose that local server
through an installed `cloudflared` tunnel.

When a coding decision needs your input, the model can call `askUserQuestion`.
Priya pauses the tool round and renders its multiple-choice options in the
terminal. Choose with arrow keys and Enter, or press Tab and submit a custom
free-text answer; the model resumes with your answers.
