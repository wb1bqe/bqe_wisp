"""Bounded continuous reception around the sstv Rust decoder.

Probe overlapping three-second windows for VIS headers, retain each detected
transmission, and decode it periodically. This preserves headers across input
block boundaries without retaining an entire recording or an entire live pass.
"""
import numpy as np
from scipy.signal import butter, sosfilt
import sstv
from .recovery import LAYOUTS, find_header, recover


class AudioDecoder:
    def __init__(self, rate, emit, report=lambda **state: None, preview=lambda image: None, combine_fragments=False):
        if not 8000 <= rate <= 192000:
            raise ValueError('SSTV requires an audio sample rate between 8000 and 192000 Hz')
        self.rate, self.emit, self.report = rate, emit, report
        self.preview = preview
        from .composite import FragmentCombiner
        self.combiner = FragmentCombiner() if combine_fragments else None
        # SSTV header, sync and picture tones occupy 1100-2300 Hz. Raw FM
        # audio can also contain strong high-frequency noise, which disrupts
        # the Rust engine's waveform-crossing frequency estimator. Filter
        # once on ingress so header probes and image decodes see identical
        # audio. Carry state across chunks; restarting it would create clicks.
        self.audio_filter = butter(6, 2700, fs=rate, output='sos')
        self.filter_state = np.zeros((len(self.audio_filter), 2))
        self.pending = np.empty(0, dtype=np.float32)
        self.window = np.empty(0, dtype=np.float32)
        self.parts = []
        self.seconds = 0
        self.last_header = -10
        self.last_decode = 0
        self.started = 0
        self.saved = False
        self.mode = None
        self.layout = None
        self.picture_start = None
        self.deadline = 0
        self.best_image = None

    def push(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or not np.all(np.isfinite(samples)):
            raise ValueError('Expected finite, mono audio samples')
        if not len(samples):
            return
        samples, self.filter_state = sosfilt(
            self.audio_filter, samples, zi=self.filter_state)
        samples = samples.astype(np.float32)
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
        if not self.parts and self.seconds - self.last_header >= 3:
            header = find_header(self.window, self.rate)
            found = [] if header else sstv.decode(self.window, self.rate)
            if header or found:
                self.parts = [self.window.copy()]
                self.started = max(0, self.seconds - len(self.window) / self.rate)
                self.last_header = self.last_decode = self.seconds
                self.saved = False
                self.mode = header[0].name if header else str(found[0].info['sstv_mode']).removeprefix('Mode.')
                self.layout = LAYOUTS[self.mode]
                self.picture_start = header[1] if header else None
                # Include actual header position, not just the advertised mode
                # number (PD120 is ~126 s, Pasokon P7 is ~406 s).
                self.deadline = self.started + (self.picture_start if header else 3) + self.layout.duration + .5
                self.best_image = found[0] if found else None
                self.report(mode=self.mode, reception='Receiving image',
                            header_audio_seconds=self.seconds,
                            header_detector='spectral VIS' if header else 'sstv engine')
                if found:
                    self.preview(found[0])
        if self.parts and not self.saved and self.seconds - self.last_decode >= 2:
            self._decode()
        # Never abandon a valid header during a fade or let missing sync make
        # one image consume the next transmission. Retain exactly this mode's
        # expected span plus a small clock/last-row allowance.
        if self.parts and self.seconds >= self.deadline:
            if not self.saved:
                self._decode(final=True)
            self.parts = []
            self.report(reception='Waiting for the next SSTV header')

    def _decode(self, final=False):
        self.last_decode = self.seconds
        images = [] if self.best_image is not None and self.best_image.info.get('sstv_complete') else sstv.decode(np.concatenate(self.parts), self.rate)
        if images:
            image = images[0]
            self.best_image = image
            self.preview(image)
        if final:
            image = self.best_image
            if self.picture_start is not None:
                timed = recover(np.concatenate(self.parts), self.rate, self.layout, self.picture_start)
                # Clean receptions retain the established engine's rendering.
                # Once sync is lost it can compress time, so use the clocked
                # image instead, including later rows after the signal returns.
                if image is None or not image.info.get('sstv_complete') or timed.info['sstv_missing_sync'] > 1:
                    image = timed
                elif not timed.info['sstv_complete']:
                    image.info['sstv_complete'] = False
                image.info['sstv_expected_seconds'] = self.layout.duration
                if image is not timed:
                    image.info['sstv_confidence'] = timed.info['sstv_confidence']
            if image is not None:
                self.preview(image)
                self.emit(image, self.started)
                if self.combiner is not None:
                    combined = self.combiner.add(image,self.started)
                    if combined is not None:
                        self.emit(combined,self.started)
            self.saved = True
            self.parts = []
            self.best_image = None
            self.report(reception='Waiting for the next SSTV header')

    def finish(self):
        if len(self.pending):
            self._block(self.pending)
            self.pending = np.empty(0, dtype=np.float32)
        if self.parts and not self.saved:
            self._decode(final=True)
