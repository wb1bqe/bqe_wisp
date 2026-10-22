"""Launch SO-127 telemetry alongside SSDV during scheduled passes."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

SO127_RECEIVER = Receiver(key='decode_so127_telemetry',
                         root=Path(__file__).resolve().parent,
                         port=8782, label='SO-127 telemetry')
