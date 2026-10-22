"""Opt-in MARMOTSat reception using the configured rig/SDR audio input."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

MARMOTSAT_RECEIVER = Receiver(key='decode_marmotsat_telemetry',
                             root=Path(__file__).resolve().parent,
                             port=8774, label='MARMOTSat UHF')
