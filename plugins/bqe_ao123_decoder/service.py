# SPDX-License-Identifier: GPL-3.0-or-later
from plugins.bqe_tlm_decoder.service import Session as AudioSession
from .dsp import AudioDecoder


class Session(AudioSession):
    file_suffixes = ('.wav', '.flac')

    def __init__(self, output):
        super().__init__(output, decoder_factory=AudioDecoder)

    def start(self, options, file=None, cleanup=False):
        from pathlib import Path
        if file and Path(file).suffix.lower() not in self.file_suffixes:
            raise ValueError('Use lossless WAV or FLAC containing linear BPSK audio or stereo I/Q; FM voice/MP3 is unsuitable')
        # Validate source options synchronously before a worker opens resources.
        AudioDecoder(int(options.get('sample_rate', 48000)), lambda packet: None, options)
        super().start(options, file, cleanup)

    def snapshot(self):
        state = super().snapshot()
        if state['state'] == 'completed' and not state['packets']:
            state['warning'] = ('No validated AO-123 packets found. Check that the source preserves BPSK '
                                '(not FM voice audio), the carrier setting, signal strength and Doppler tuning.')
        return state
