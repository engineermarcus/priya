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

Press `Ctrl+C` to exit. In the input field, enter `q` to quit.

## Tools

The model can call `bash` for local shell work and `agentjob` to delegate a
background coding task. Expand a running `bash` tool in the UI to follow its
stdout and stderr; the final result is appended after the live log. `agentjob`
expects its runner at `~/agent/job_runner.py`.
