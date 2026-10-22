"""Opt-in LASARsat reception through BQE's managed decoder lifecycle."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

LASARSAT_RECEIVER = Receiver(key='decode_lasarsat_telemetry',
                            root=Path(__file__).resolve().parent,
                            port=8778, label='LASARsat telemetry')
