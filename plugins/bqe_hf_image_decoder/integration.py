"""Dependency-free preset/pass lifecycle bridge."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver as ImageReceiver


class Receiver(ImageReceiver):
    def __init__(self, root=Path(__file__).resolve().parent):
        super().__init__('decode_hf_images', root, 8776, 'HF digital image')

    def decoder_args(self, config):
        profile = config.get('hf_image_profile', 'auto')
        if profile not in ('auto', 'freedv', 'hamdrm'):
            raise ValueError('hf_image_profile must be auto, freedv, or hamdrm')
        return ['--profile', profile]


HF_IMAGE_RECEIVER = Receiver()
