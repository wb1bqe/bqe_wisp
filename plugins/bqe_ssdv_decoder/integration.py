"""Start RS61 digital-image reception for opted-in passes and presets."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

SSDV_RECEIVER = Receiver(key='decode_ssdv_images', root=Path(__file__).resolve().parent,
                         port=8771, label='SSDV')
