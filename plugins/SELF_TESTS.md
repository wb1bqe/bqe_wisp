# Plugin diagnostics

Open **Help → Perform environment diagnostics** for the usual quick checks.
Select **Run plugin self-tests**, then **Run diagnostics**, to include offline
checks for the audio recorder, SSTV, telemetry, SSDV, SDR, and LRPT plugins.
**Save as text** includes the test results and failure details.

These are 19 selected checks using synthetic signals and temporary files.
They do not open radio or audio hardware, access the network, or launch external
decoders. LRPT checks validate its integration using a simulated decoder;
they do not certify an installed SatDump binary or live satellite reception.

Missing dependencies, test files, or Python runtimes are warnings. Tests use
each plugin's virtual environment when available, otherwise BQE's Python.
Nothing is installed automatically. Assertion failures and timeouts are failures.
Each suite has a 20-second limit. Checks skip while a satellite pass is active,
and a running test worker stops if a pass starts or BQE shuts down.

For maintainers: `plugins/self_tests.py` lists exact test methods deliberately.
Do not replace it with general test discovery: development suites can include
hardware, server, and workstation-specific checks. Keep new selected methods
offline and compatible with Windows and Linux. Runner regression tests live in
`tests/test_plugin_self_tests.py`.
