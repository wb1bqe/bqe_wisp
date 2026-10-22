#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""MONITOR-3 / MONITOR-4: live USP telemetry and recorded FM audio."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import main as telemetry_main
from plugins.bqe_monitor_decoder.service import Session


def main():
    return telemetry_main(root=ROOT, session_factory=Session, port=8777,
                          label='Monitor telemetry', protocol='usp', protocols=('usp',))


if __name__ == '__main__':
    raise SystemExit(main())
