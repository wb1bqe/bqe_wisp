"""HF reception through BQE's common audio capture and gallery lifecycle."""
from plugins.bqe_sstv_decoder.service import Session as ImageSession, devices, gallery_settings
from .decoder import AudioDecoder

# Load SciPy's native libraries before the managed-owner thread blocks on
# stdin. On Windows, loading them later in the decode worker can deadlock
# with that pipe read, freezing capture and the HTTP controls together.


class Session(ImageSession):
    image_prefix = 'hf-digital'
    mode_key = 'ssdv_mode'
    complete_key = 'ssdv_complete'
    waiting_message = 'Waiting for HF digital image packets'
    no_image_message = 'No validated HF digital image received.'

    def new_decoder(self, rate, options):
        return AudioDecoder(rate, self._save, self.update, self.preview,
                            profile=options.get('profile', 'auto'))

    def image_metadata(self, image):
        return {key: image.info.get(key) for key in
                ('file_id', 'packets', 'missing_packets', 'end_received', 'callsign')}
