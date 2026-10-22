#!/usr/bin/env python3
"""SO-127 telemetry from flat FM discriminator audio or recordings."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import main as telemetry_main
from plugins.bqe_so127_decoder.service import Session


def main():
    return telemetry_main(root=ROOT, session_factory=Session, port=8782,
                          label='SO-127 telemetry', protocol='so127',
                          protocols=('so127',), bauds=(200, 800, 1600))


if __name__ == '__main__':
    raise SystemExit(main())
