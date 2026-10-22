"""Opt-in OTP-2 reception through BQE's managed decoder lifecycle."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver

OTP2_RECEIVER = Receiver(key='decode_otp2_telemetry',
                            root=Path(__file__).resolve().parent,
                            port=8779, label='OTP-2 telemetry')
