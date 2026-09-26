"""Bounded continuous reception around the sstv Rust decoder.

Probe overlapping three-second windows for VIS headers, retain each detected
transmission, and decode it periodically. This preserves headers across input
block boundaries without retaining an entire recording or an entire live pass.
"""
import numpy as np
import sstv


class AudioDecoder:
    def __init__(self, rate, emit, report=lambda **state: None, preview=lambda image: None):
        if not 8000 <= rate <= 192000:
            raise ValueError('SSTV requires an audio sample rate between 8000 and 192000 Hz')
        self.rate, self.emit, self.report = rate, emit, report
        self.preview = preview
        self.pending = np.empty(0, dtype=np.float32)
        self.window = np.empty(0, dtype=np.float32)
        self.parts = []
        self.seconds = 0
        self.last_header = -10
        self.last_decode = 0
        self.started = 0
        self.saved = False
        self.mode = None

    def push(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or not np.all(np.isfinite(samples)):
            raise ValueError('Expected finite, mono audio samples')
        self.pending = np.concatenate((self.pending, samples))
        while len(self.pending) >= self.rate:
            block, self.pending = self.pending[:self.rate], self.pending[self.rate:]
            self._block(block)

    def _block(self, block):
        self.seconds += len(block) / self.rate
        self.window = np.concatenate((self.window, block))[-3*self.rate:]
        if self.parts:
            self.parts.append(block.copy())
        # A header can remain in the probe for multiple iterations. Cooldown
        # prevents treating these observations as separate transmissions.
        if self.seconds - self.last_header >= 3:
            found = sstv.decode(self.window, self.rate)
            if found:
                if self.parts and not self.saved:
                    self._decode(final=True)
                self.parts = [self.window.copy()]
                self.started = max(0, self.seconds - len(self.window) / self.rate)
                self.last_header = self.last_decode = self.seconds
                self.saved = False
                self.mode = str(found[0].info['sstv_mode']).removeprefix('Mode.')
                self.report(mode=self.mode, reception='Receiving image')
                self.preview(found[0])
        if self.parts and not self.saved and self.seconds - self.last_decode >= 2:
            self._decode()
        # Pasokon P7 takes about 408 seconds. Bound memory even if a
        # false header or a damaged transmission never produces a complete image.
        if self.parts and self.seconds - self.started > 450:
            if not self.saved:
                self._decode(final=True)
            self.parts = []
            self.report(reception='Waiting for the next SSTV header')

    def _decode(self, final=False):
        self.last_decode = self.seconds
        images = sstv.decode(np.concatenate(self.parts), self.rate)
        if images:
            image = images[0]
            self.preview(image)
            complete = bool(image.info.get('sstv_complete', False))
            if complete or final:
                self.emit(image, self.started)
                self.saved = True
                # Retain the detection window separately; the full audio is no
                # longer needed once this image has been saved.
                self.parts = []
                self.report(reception='Waiting for the next SSTV header')

    def finish(self):
        if len(self.pending):
            self._block(self.pending)
            self.pending = np.empty(0, dtype=np.float32)
        if self.parts and not self.saved:
            self._decode(final=True)
