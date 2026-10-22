# SPDX-License-Identifier: GPL-3.0-or-later
from plugins.bqe_tlm_decoder.service import Session as AudioSession
from .dsp import AudioDecoder
from .igate import IGate
from .igate_settings import SettingsStore
import uuid


class Session(AudioSession):
    def __init__(self, output, store=None, gate_factory=IGate):
        self.settings_store = store or SettingsStore()
        self.gate_factory = gate_factory
        self.igate = None
        self.reception_context = 'manual'
        super().__init__(output, decoder_factory=self.decoder)

    def decoder(self, rate, emit, options):
        def receive(packet):
            packet['upload_id'] = uuid.uuid4().hex
            packet['reception_context'] = self.reception_context
            emit(packet)
            gate = self.igate
            if gate:
                gate.submit(packet)
        return AudioDecoder(rate, receive, options)

    def input_ready(self, source, recorder):
        # This hook is called only for a live capture, after opening its source.
        # SDR source metadata is attached to that same audio HTTP connection.
        if self.igate:
            if hasattr(source, 'sdr_url') and not getattr(recorder, 'live_rf', False):
                self.igate.update(state='blocked', message='SDR file/demo or unknown source: uploads blocked')
            else:
                self.igate.start()

    def _run(self, options, file, cleanup):
        self.reception_context = 'recording' if file else options.get('reception_context', 'manual')
        self.igate = None if file else self.gate_factory(self.settings_store)
        try:
            super()._run(options, file, cleanup)
        finally:
            if self.igate:
                self.igate.close()

    def snapshot(self):
        state = super().snapshot()
        state['igate'] = self.igate.snapshot() if self.igate else dict(state='inactive',message='File decoding never uploads')
        return state

    def upload_settings(self, changes=None):
        return self.settings_store.public() if changes is None else self.settings_store.save(changes)
