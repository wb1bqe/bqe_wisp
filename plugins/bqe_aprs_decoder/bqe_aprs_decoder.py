#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""APRS from live radio/SDR audio or recorded FM audio."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import main as telemetry_main
from plugins.bqe_aprs_decoder.service import Session


def main():
    def configure_parser(parser):
        parser.add_argument('--reception-context', choices=('satellite_pass', 'idle_preset', 'manual'),
                            default='manual', help='Reception origin supplied by the BQE scheduler')
    return telemetry_main(root=ROOT, session_factory=Session, port=8781,
                          label='APRS', protocol='aprs', protocols=('aprs',), bauds=(1200,),
                          configure_parser=configure_parser)


if __name__ == '__main__':
    raise SystemExit(main())
