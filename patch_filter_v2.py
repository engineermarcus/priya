#!/usr/bin/env python3
"""Fix PlaybackEchoFilter dropping to NS-only idle path mid-tail.

Tracks actual queued speaker duration (from bytes handed to add_playback_24khz,
which are the same bytes written to aplay) instead of guessing a fixed
hangover. The AEC engine stays live on zero-filled far frames until that
tracked duration has elapsed, which always matches however much audio
aplay/ALSA still has physically queued -- however large the trailing chunk.

Usage: python3 patch_filter_v2.py [path/to/filter.py]
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/marcus/priya/filter.py"

with open(path, "r") as f:
    src = f.read()

if "_queued_playback_samples" in src:
    print(f"{path}: already patched, skipping")
    sys.exit(0)

# 1. __init__: add a counter for how many (16kHz-equivalent) far-end samples
#    are still physically queued/playing in aplay's ALSA buffer.
src, n = re.subn(
    r"(        self\._far = deque\(\)\n)",
    r"        # Samples still physically queued/playing via aplay, tracked in\n"
    r"        # the 16 kHz domain. Drained in process() at mic-capture rate,\n"
    r"        # NOT at _far-queue-drain rate -- those differ whenever a\n"
    r"        # trailing network chunk lands after send-side speech already\n"
    r"        # stopped, so this is what decides the AEC/idle handoff.\n"
    r"        self._queued_playback_samples = 0\n"
    r"\1",
    src,
    count=1,
)
assert n == 1, "could not patch __init__"

# 2. reset(): clear the counter too
src, n = re.subn(
    r"(        self\._far\.clear\(\)\n)",
    r"\1        self._queued_playback_samples = 0\n",
    src,
    count=1,
)
assert n == 1, "could not patch reset()"

# 3. add_playback_24khz(): count queued duration as audio arrives (in the
#    16kHz-equivalent sample domain, i.e. len(converted)), not just enqueue it.
old_extend = "        self._far.extend(converted)\n"
new_extend = (
    "        self._far.extend(converted)\n"
    "        self._queued_playback_samples += len(converted)\n"
)
assert src.count(old_extend) == 1, "could not find _far.extend() call"
src = src.replace(old_extend, new_extend, 1)

# 4. process(): drain the queued-duration counter by exactly what real time
#    elapsed this frame (len(near) samples @ sample_rate), and gate the
#    AEC/idle handoff on that counter instead of "does this exact frame
#    have a nonzero far buffer".
old_tail = '''        if np.any(far):
            cleaned = self.processor.process(near, far)
        else:
            cleaned = self.idle_processor.process(near)
        return cleaned.astype("<i2", copy=False).tobytes()'''

new_tail = '''        # This frame represents len(near) samples of real elapsed time,
        # regardless of how many _far samples were actually available --
        # drain the queued-playback clock by that same amount so it tracks
        # wall-clock speaker drain, not far-buffer drain.
        self._queued_playback_samples = max(
            0, self._queued_playback_samples - len(near)
        )
        if self._queued_playback_samples > 0:
            # Speaker is still (or was very recently) physically playing
            # audio -- keep the real AEC engine live, even on a zero-filled
            # far frame from a transient network underrun, so its adapted
            # echo model doesn't drop right when it's needed most.
            cleaned = self.processor.process(near, far)
        else:
            cleaned = self.idle_processor.process(near)
        return cleaned.astype("<i2", copy=False).tobytes()'''

assert old_tail in src, "could not find process() tail to patch"
src = src.replace(old_tail, new_tail, 1)

with open(path, "w") as f:
    f.write(src)

print(f"{path}: patched (AEC stays live until tracked queued-playback duration hits 0)")
