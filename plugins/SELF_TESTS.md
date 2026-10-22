# Plugin diagnostics

Open **Help → Perform environment diagnostics** for the usual quick checks.
Select **Run plugin self-tests**, then **Run diagnostics**, to include offline
checks for the audio recorder, APRS, SSTV, telemetry, SSDV, SDR, and LRPT plugins.
**Save as text** includes the test results and failure details.

SO-127 telemetry checks include the operator's reference packet CRCs,
200/800/1600-baud audio recovery, both polarities and corruption rejection.

These are selected checks using synthetic signals, a public UMKA-1 packet
fixture, and temporary files. SSDV checks include UMKA-1 USP and Geoscan v1/v2,
including complete image recovery through simulated RTL-SDR IQ and BQE's demodulator.
They do not open radio or audio hardware, access the network, or launch external
decoders. LRPT checks validate its integration using a simulated decoder;
they do not certify an installed SatDump binary or live satellite reception.

Missing dependencies, test files, or Python runtimes are warnings. Tests use
each plugin's virtual environment when available, otherwise BQE's Python.
Nothing is installed automatically. Assertion failures and timeouts are failures.
English transcription checks use a fake recognizer to validate radio text,
timestamps, silence and failure handling; they do not load/download speech models.
Each suite has a 20-second limit. Checks skip while a satellite pass is active,
and a running test worker stops if a pass starts or BQE shuts down.

For maintainers: `plugins/self_tests.py` lists exact test methods deliberately.
Do not replace it with general test discovery: development suites can include
hardware, server, and workstation-specific checks. Keep new selected methods
offline and compatible with Windows and Linux. Runner regression tests live in
`tests/test_plugin_self_tests.py`.

AO-123 checks exercise synthetic CCSDS FEC/polarity, short-TM packet reassembly
and corruption rejection, without radio hardware or network access.
