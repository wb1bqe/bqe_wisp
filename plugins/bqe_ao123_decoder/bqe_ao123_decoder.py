#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""AO-123: live or recorded 9600 BPSK telemetry (not FM voice audio)."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import main as telemetry_main
from plugins.bqe_ao123_decoder.service import Session


def arguments(parser):
    parser.description = __doc__
    parser.add_argument('--input-format', choices=('audio', 'iq'), default='audio',
                        help='Wide linear audio, or two-channel I/Q WAV (I left, Q right)')
    parser.add_argument('--carrier', type=float, default=12000,
                        help='BPSK carrier in audio, Hz (default 12000); ignored for baseband I/Q')


def main():
    return telemetry_main(root=ROOT, session_factory=Session, port=8783,
                          label='AO-123 telemetry', protocol='ccsds', protocols=('ccsds',),
                          bauds=(9600,), configure_parser=arguments)


if __name__ == '__main__':
    raise SystemExit(main())
