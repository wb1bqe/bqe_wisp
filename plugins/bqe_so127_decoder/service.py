"""Shared capture, upload, managed shutdown and JSONL storage."""
from plugins.bqe_tlm_decoder.service import Session as AudioSession
from .dsp import AudioDecoder


class Session(AudioSession):
    def __init__(self, output):
        super().__init__(output, decoder_factory=AudioDecoder, save_kiss=False)
