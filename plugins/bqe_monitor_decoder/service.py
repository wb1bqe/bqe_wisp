# SPDX-License-Identifier: GPL-3.0-or-later
from plugins.bqe_tlm_decoder.service import Session as AudioSession
from .dsp import AudioDecoder


class Session(AudioSession):
    def __init__(self, output):
        super().__init__(output, decoder_factory=AudioDecoder)
