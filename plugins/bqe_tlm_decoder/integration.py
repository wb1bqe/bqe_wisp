"""Launch telemetry alongside a satellite pass, with orderly audio shutdown."""
from pathlib import Path

# This bridge is dependency-free; importing it does not load the SSTV engine.
from plugins.bqe_sstv_decoder.integration import Receiver

TELEMETRY_RECEIVER = Receiver(key='decode_telemetry', root=Path(__file__).resolve().parent,
                              port=8769, label='Telemetry')
