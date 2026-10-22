"""Managed APRS reception from rig audio or the BQE SDR bridge."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver


class APRSReceiver(Receiver):
    def decoder_args(self, config):
        bauds = config.get('aprs_bauds', [1200])
        if not isinstance(bauds, list) or not bauds or any(type(b) is not int or b not in (1200, 9600) for b in bauds):
            raise ValueError('aprs_bauds must be a list containing 1200 and/or 9600')
        args = ['--bauds', *(str(b) for b in dict.fromkeys(bauds))]
        context = config.get('_aprs_reception_context')
        if context in ('satellite_pass', 'idle_preset'):
            args.extend(['--reception-context', context])
        return args


APRS_RECEIVER = APRSReceiver(key='decode_aprs', root=Path(__file__).resolve().parent,
                             port=8781, label='APRS')
