#!/usr/bin/env python3
"""Fix: interrupting the model (barge-in) makes it hear its own trailing
audio as user input.

Root cause: on interrupt, live_cli.py calls discard_playback() (kills the
old aplay process, which can still be physically draining its ALSA buffer
for a short window) and mic_processor.reset() in the same breath. reset()
wipes PlaybackEchoFilter's state, including the queued-playback clock added
in the previous patch -- so filter.process() sees an all-zero far signal on
the very next mic frame and immediately drops to the NS-only idle path,
right as leftover playback (and the user's real interrupting speech) are
both hitting the mic at once. This is the single worst moment to lose echo
cancellation, and it's an unconditional reset that ignores what's actually
still queued.

Fix: reset() no longer blindly zeroes the queued-playback clock. Instead it
takes a `keep_hangover_ms` argument; live_cli.py's interrupt path passes a
short forced hangover (default 300ms) covering the kill/respawn transition,
while a full session reset (no user in the middle of talking) can still
clear to zero.

Usage: python3 patch_filter_interrupt.py [path/to/filter.py] [path/to/live_cli.py]
"""
import re
import sys

filter_path = sys.argv[1] if len(sys.argv) > 1 else "/home/marcus/priya/filter.py"
cli_path = sys.argv[2] if len(sys.argv) > 2 else "/home/marcus/priya/live_cli.py"

# ---------------------------------------------------------------- filter.py
with open(filter_path, "r") as f:
    fsrc = f.read()

if "keep_hangover_ms" in fsrc:
    print(f"{filter_path}: already patched, skipping")
else:
    if "_queued_playback_samples" not in fsrc:
        print(
            f"ERROR: {filter_path} doesn't have the queued-playback-clock patch "
            "applied yet. Run patch_filter_v2.py on it first."
        )
        sys.exit(1)

    old_reset = '''    def reset(self):
        self.processor.reset()
        self.idle_processor.reset()
        self._far.clear()
        self._queued_playback_samples = 0
        self._resample_tail = np.empty(0, dtype=np.int16)'''

    new_reset = '''    def reset(self, keep_hangover_ms=0):
        """Reset filter state.

        keep_hangover_ms: instead of zeroing the queued-playback clock,
        pin it to at least this many ms. Use this on a barge-in interrupt,
        where the old aplay process is being killed/respawned but its ALSA
        buffer may still be physically draining -- losing AEC at that exact
        moment means leftover playback gets read back as user speech. A
        full idle reset (nothing was playing) should pass 0.
        """
        self.processor.reset()
        self.idle_processor.reset()
        self._far.clear()
        self._queued_playback_samples = int(
            self.sample_rate * keep_hangover_ms / 1000
        )
        self._resample_tail = np.empty(0, dtype=np.int16)'''

    assert fsrc.count(old_reset) == 1, "could not find filter.py reset() to patch"
    fsrc = fsrc.replace(old_reset, new_reset, 1)

    with open(filter_path, "w") as f:
        f.write(fsrc)
    print(f"{filter_path}: patched reset() to accept keep_hangover_ms")

# ---------------------------------------------------------------- live_cli.py
with open(cli_path, "r") as f:
    csrc = f.read()

if "keep_hangover_ms=" in csrc:
    print(f"{cli_path}: already patched, skipping")
else:
    # 1. MicrophoneProcessor.reset(): pass through a hangover argument.
    old_mp_reset = '''    def reset(self):
        self.filter.reset()
        self.speech_active = False
        self.silent_frames = 0'''
    new_mp_reset = '''    def reset(self, keep_hangover_ms=0):
        self.filter.reset(keep_hangover_ms=keep_hangover_ms)
        self.speech_active = False
        self.silent_frames = 0'''
    assert csrc.count(old_mp_reset) == 1, "could not find MicrophoneProcessor.reset()"
    csrc = csrc.replace(old_mp_reset, new_mp_reset, 1)

    # 2. interrupt(): this is the barge-in path -- old aplay is being killed
    #    while it may still be physically draining. Force a hangover instead
    #    of zeroing.
    old_interrupt_block = '''        self.discard_playback()
        if self._active_bash_cancel is not None:
            self._active_bash_cancel.set()
        self._resolve_pending_question({"cancelled": True})
        self._resolve_pending_edit({"cancelled": True})
        if self.mic_processor is not None:
            self.mic_processor.reset()'''
    new_interrupt_block = '''        self.discard_playback()
        if self._active_bash_cancel is not None:
            self._active_bash_cancel.set()
        self._resolve_pending_question({"cancelled": True})
        self._resolve_pending_edit({"cancelled": True})
        if self.mic_processor is not None:
            # Barge-in: the old aplay process is being torn down but its
            # ALSA buffer may still be physically playing out audio for a
            # short window. Keep the real AEC engine engaged through that
            # window instead of dropping to NS-only right as the user's
            # interrupting speech and the leftover playback both hit the
            # mic at once.
            self.mic_processor.reset(keep_hangover_ms=300)'''
    assert csrc.count(old_interrupt_block) == 1, "could not find interrupt() reset call"
    csrc = csrc.replace(old_interrupt_block, new_interrupt_block, 1)

    # 3. was_interrupted branch in _receive_text: same situation, same fix.
    old_wi_block = '''            self.discard_playback()
                            if self.mic_processor is not None:
                                self.mic_processor.reset()'''
    new_wi_block = '''            self.discard_playback()
                            if self.mic_processor is not None:
                                self.mic_processor.reset(keep_hangover_ms=300)'''
    assert csrc.count(old_wi_block) == 1, "could not find was_interrupted reset call"
    csrc = csrc.replace(old_wi_block, new_wi_block, 1)

    with open(cli_path, "w") as f:
        f.write(csrc)
    print(f"{cli_path}: patched interrupt paths to pass keep_hangover_ms=300")

print("Done.")
