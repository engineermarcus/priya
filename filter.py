"""Reference-aware microphone cleanup for Priya's live audio path.

This intentionally uses Priya's *actual playback PCM* as the identity signal,
not a biometric voiceprint.  A voiceprint is both less reliable (a person can
sound similar to a synthetic voice) and unsuitable for deciding whether to
discard live speech.  The render reference is exact, so WebRTC AEC can remove
it while retaining a person who speaks at the same time.
"""

from collections import deque

import numpy as np
from pywebrtc_audio import AudioProcessor


class PlaybackEchoFilter:
    """Align model playback with microphone capture for WebRTC AEC/NS."""

    def __init__(self, sample_rate=16000, stream_delay_ms=60):
        self.sample_rate = sample_rate
        self.processor = AudioProcessor(
            sample_rate=sample_rate,
            echo_cancellation=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=False,
            ns_level=2,
            # This is a hint for the speaker-buffer-to-microphone delay. AEC3
            # continues estimating it, so it remains safe across devices.
            stream_delay_ms=stream_delay_ms,
        )
        # Do not run the echo canceller against an all-zero render signal
        # between responses. Noise suppression is still useful then, while a
        # dedicated non-AEC path avoids degrading quiet real speech/VAD.
        self.idle_processor = AudioProcessor(
            sample_rate=sample_rate,
            echo_cancellation=False,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=False,
            ns_level=2,
        )
        # Samples still physically queued/playing via aplay, tracked in
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
        self._resample_tail = np.empty(0, dtype=np.int16)

    def add_playback_24khz(self, pcm_24khz):
        """Queue the exact 24 kHz PCM sent to aplay as AEC render reference."""
        samples = np.frombuffer(pcm_24khz, dtype="<i2")
        if len(self._resample_tail):
            samples = np.concatenate((self._resample_tail, samples))
        # 24 kHz -> 16 kHz is a 3:2 conversion. Averaging the latter two
        # source samples is a cheap anti-aliasing step for speech playback.
        usable = len(samples) - (len(samples) % 3)
        self._resample_tail = samples[usable:].copy()
        if not usable:
            return
        triples = samples[:usable].reshape(-1, 3).astype(np.int32)
        converted = np.empty(triples.shape[0] * 2, dtype=np.int16)
        converted[0::2] = triples[:, 0]
        converted[1::2] = ((triples[:, 1] + triples[:, 2]) // 2).astype(np.int16)
        self._far.extend(converted)
        self._queued_playback_samples += len(converted)

    def process(self, microphone_pcm):
        """Return AEC/NS-cleaned 16 kHz microphone PCM."""
        near = np.frombuffer(microphone_pcm, dtype="<i2")
        if len(self._far) >= len(near):
            far = np.fromiter(
                (self._far.popleft() for _ in range(len(near))),
                dtype=np.int16,
                count=len(near),
            )
        else:
            # AEC must receive equal-sized capture/render frames. It can
            # reacquire after a short render underrun on the next frame.
            far = np.zeros(len(near), dtype=np.int16)
        # This frame represents len(near) samples of real elapsed time,
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
        return cleaned.astype("<i2", copy=False).tobytes()
