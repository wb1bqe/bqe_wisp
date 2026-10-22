"""Opt-in RS-61 reception through BQE's managed decoder lifecycle."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

RS61_RECEIVER = Receiver(key='decode_rs61_telemetry',
                            root=Path(__file__).resolve().parent,
                            port=8780, label='RS-61 telemetry')
