#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""RS-61 9600-baud GFSK/Geoscan telemetry from FM discriminator audio."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import main as telemetry_main
from plugins.bqe_rs61_decoder.service import Session


def main():
    return telemetry_main(root=ROOT, session_factory=Session, port=8780,
                          label='RS-61 telemetry', protocol='geoscan', protocols=('geoscan',),
                          bauds=(9600,))


if __name__ == '__main__':
    raise SystemExit(main())
