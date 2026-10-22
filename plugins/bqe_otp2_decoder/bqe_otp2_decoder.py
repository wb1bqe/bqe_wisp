#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental OTP-2 GFSK/AX.25 packet recovery from FM audio."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import main as telemetry_main
from plugins.bqe_otp2_decoder.service import Session


def main():
    return telemetry_main(root=ROOT, session_factory=Session, port=8779,
                          label='OTP-2 telemetry', protocol='ax25', protocols=('ax25',),
                          bauds=(4800,))


if __name__ == '__main__':
    raise SystemExit(main())
