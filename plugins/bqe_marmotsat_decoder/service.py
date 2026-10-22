# SPDX-License-Identifier: GPL-3.0-or-later
"""Use BQE's established capture/upload lifecycle and JSONL packet storage."""
from plugins.bqe_tlm_decoder.service import Session as AudioSession
from .dsp import AudioDecoder


class Session(AudioSession):
    def __init__(self, output):
        # These are OpenLST packets, not AX.25; do not mislabel them as KISS.
        super().__init__(output, decoder_factory=AudioDecoder, save_kiss=False)
