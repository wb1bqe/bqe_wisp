"""Managed Monitor reception from rig audio or the BQE SDR bridge."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

MONITOR_RECEIVER = Receiver(key='decode_monitor_telemetry',
                            root=Path(__file__).resolve().parent,
                            port=8777, label='Monitor telemetry')
