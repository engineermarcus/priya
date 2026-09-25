#!/usr/bin/env python3
"""One-shot patch: replaces the v1 flat-500ms hangover with a queued-playback
clock sized from actual audio bytes, and fixes the barge-in interrupt path
that was zeroing that clock mid-speaker-drain.

Matches the CURRENT on-disk filter.py (already has the 500ms hangover patch)
and live_cli.py from Priya. Safe to re-run (idempotent).

Usage: python3 patch_filter_final.py [filter.py] [live_cli.py]
"""
import sys

filter_path = sys.argv[1] if len(sys.argv) > 1 else "/home/marcus/priya/filter.py"
cli_path = sys.argv[2] if len(sys.argv) > 2 else "/home/marcus/priya/live_cli.py"

# ---------------------------------------------------------------- filter.py
with open(filter_path, "r") as f:
    fsrc = f.read()

if "keep_hangover_ms" in fsrc:
    print(f"{filter_path}: already patched, skipping")
else:
    old_init_tail = '''        # Hangover: keep the real AEC engine running on zero-filled
        # far frames for this long after render silence starts, since
        # ALSA/aplay is still physically draining the speaker buffer.
        self._hangover_samples = int(sample_rate * 0.5)  # 500ms
        self._silence_run_samples = 0
        self._far = deque()
        self._resample_tail = np.empty(0, dtype=np.int16)

    def reset(self):
        self.processor.reset()
        self.idle_processor.reset()
        self._far.clear()
        self._silence_run_samples = 0
        self._resample_tail = np.empty(0, dtype=np.int16)'''

    new_init_tail = '''        # Samples still physically queued/playing via aplay, tracked in
        # the 16 kHz domain and drained in process() at real elapsed time
        # (len(near) per frame) -- not at _far-queue-drain rate, since those
        # differ whenever a trailing network chunk lands late. This is what
        # decides the AEC/idle handoff, both on normal end-of-turn silence
        # and on barge-in interrupts.
        self._queued_playback_samples = 0
        self._far = deque()
        self._resample_tail = np.empty(0, dtype=np.int16)

    def reset(self, keep_hangover_ms=0):
        """Reset filter state.

        keep_hangover_ms: pin the queued-playback clock to at least this
        many ms instead of zeroing it. Use this on a barge-in interrupt,
        where the old aplay process is being killed/respawned but its ALSA
        buffer may still be physically draining -- losing AEC at that exact
        moment means leftover playback gets read back as user speech right
        while the user is actually talking. A full idle reset (nothing was
        playing) should pass 0 (the default).
        """
        self.processor.reset()
        self.idle_processor.reset()
        self._far.clear()
        self._queued_playback_samples = int(
            self.sample_rate * keep_hangover_ms / 1000
        )
        self._resample_tail = np.empty(0, dtype=np.int16)'''

    assert fsrc.count(old_init_tail) == 1, "filter.py: init/reset block not found as expected"
    fsrc = fsrc.replace(old_init_tail, new_init_tail, 1)

    old_extend = "        self._far.extend(converted)\n"
    new_extend = (
        "        self._far.extend(converted)\n"
        "        self._queued_playback_samples += len(converted)\n"
    )
    assert fsrc.count(old_extend) == 1, "filter.py: _far.extend() call not found"
    fsrc = fsrc.replace(old_extend, new_extend, 1)

    old_process_tail = '''        if np.any(far):
            self._silence_run_samples = 0
        else:
            self._silence_run_samples += len(far)

        if self._silence_run_samples < self._hangover_samples:
            # Still inside the hangover window (or actively playing): keep
            # feeding the real AEC engine, even with a zero far frame, so
            # its adapted echo model stays live through the speaker's tail.
            cleaned = self.processor.process(near, far)
        else:
            cleaned = self.idle_processor.process(near)
        return cleaned.astype("<i2", copy=False).tobytes()'''

    new_process_tail = '''        # This frame represents len(near) samples of real elapsed time,
        # regardless of how many _far samples were actually available --
        # drain the queued-playback clock by that same amount so it tracks
        # wall-clock speaker drain, not far-buffer drain.
        self._queued_playback_samples = max(
            0, self._queued_playback_samples - len(near)
        )
        if self._queued_playback_samples > 0:
            # Speaker is still (or was very recently) physically playing
            # audio -- keep the real AEC engine live, even on a zero-filled
            # far frame, so its adapted echo model doesn't drop right when
            # it's needed most (end-of-turn tail, or mid-barge-in).
            cleaned = self.processor.process(near, far)
        else:
            cleaned = self.idle_processor.process(near)
        return cleaned.astype("<i2", copy=False).tobytes()'''

    assert fsrc.count(old_process_tail) == 1, "filter.py: process() tail not found as expected"
    fsrc = fsrc.replace(old_process_tail, new_process_tail, 1)

    with open(filter_path, "w") as f:
        f.write(fsrc)
    print(f"{filter_path}: patched")

# ---------------------------------------------------------------- live_cli.py
with open(cli_path, "r") as f:
    csrc = f.read()

if "keep_hangover_ms=" in csrc:
    print(f"{cli_path}: already patched, skipping")
else:
    old_mp_reset = '''    def reset(self):
        self.filter.reset()
        self.speech_active = False
        self.silent_frames = 0'''
    new_mp_reset = '''    def reset(self, keep_hangover_ms=0):
        self.filter.reset(keep_hangover_ms=keep_hangover_ms)
        self.speech_active = False
        self.silent_frames = 0'''
    assert csrc.count(old_mp_reset) == 1, "live_cli.py: MicrophoneProcessor.reset() not found"
    csrc = csrc.replace(old_mp_reset, new_mp_reset, 1)

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
            # short window, right while the user is actively speaking into
            # the mic. Keep the real AEC engine engaged through that window
            # instead of dropping to NS-only at the worst possible moment.
            self.mic_processor.reset(keep_hangover_ms=300)'''
    assert csrc.count(old_interrupt_block) == 1, "live_cli.py: interrupt() reset call not found"
    csrc = csrc.replace(old_interrupt_block, new_interrupt_block, 1)

    old_wi_block = '''            self.discard_playback()
                            if self.mic_processor is not None:
                                self.mic_processor.reset()'''
    new_wi_block = '''            self.discard_playback()
                            if self.mic_processor is not None:
                                self.mic_processor.reset(keep_hangover_ms=300)'''
    assert csrc.count(old_wi_block) == 1, "live_cli.py: was_interrupted reset call not found"
    csrc = csrc.replace(old_wi_block, new_wi_block, 1)

    with open(cli_path, "w") as f:
        f.write(csrc)
    print(f"{cli_path}: patched")

print("Done.")
