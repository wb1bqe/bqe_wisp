"""Start RS61 digital-image reception for opted-in passes and presets."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

class SSDVReceiver(Receiver):
    def decoder_args(self, config):
        profile = config.get('ssdv_profile', 'rs61')
        if profile not in ('rs61', 'hades-sa', 'umka-1', 'geoscan'):
            raise ValueError('ssdv_profile must be rs61, hades-sa, umka-1 or geoscan')
        return ['--profile', profile]


SSDV_RECEIVER = SSDVReceiver(key='decode_ssdv_images', root=Path(__file__).resolve().parent,
                         port=8771, label='SSDV')
