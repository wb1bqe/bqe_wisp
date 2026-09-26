# BQE LRPT weather image decoder

Windows/Linux Meteor-M2-3 and M2-4 LRPT reception using the official **SatDump**
decoder. BQE owns the pass lifecycle, captures full-bandwidth complex IQ, preserves
SigMF metadata, and launches SatDump after capture ends. A local gallery shows
the recovered channels/composites and decoding logs. This is an IQ plugin, not
an audio decoder: SSTV, speaker outputs and BQE SDR's 48 kHz audio bridge cannot
carry LRPT. No virtual audio cable is needed.

## Supported scope

- Meteor M2-x LRPT 72 and 80 ksym/s, using SatDump's OQPSK pipelines.
- Direct RTL-SDR USB and rtl_tcp IQ capture; shared driver/device settings from
  `my_rig.yaml`'s `sdr` mapping.
- Single-center SigMF replay: unsigned 8-bit, signed 16-bit LE, or float32 LE IQ.
- Automatic image generation **after the pass**, not progressive live pictures.
- IQ retention, explicit no-image/failure status, per-job logs, and browser gallery.

The original Meteor-M2 and M2-2 are not presented as current LRPT targets.
NOAA 15/18/19 APT has been decommissioned. This plugin does not implement
HF WEFAX, GOES HRIT, HRPT or every weather-satellite format. Those require
other pipelines and, often, different RF equipment.

Satellite transmitter status, frequency and rate can change. Check the
[SatDump community status report](https://ub8qbd.satdump.org/wx_report_new.html)
and recent observations before scheduling; the status page itself can lag.
137.100 and 137.900 MHz are editable starting values, not a guarantee of a
transmitter currently being active. The example file has scheduling off; this
workstation's `satellites.yaml` has **meteor-m2-4-lrpt** autoscheduling enabled
at 137.100 MHz / 72 ksym/s. Its catalog number is 59051 and the orbital-element
updater includes it. M2-3 is provided as an example rather than scheduled because
the available community status report lists its LRPT transmitter as off.

## Windows

On this workstation the isolated Python environment and official portable
**SatDump 1.2.2 x64** have been installed under this plugin. WinUSB and the RTL-SDR
library are reused from BQE SDR. Double-click `start_windows.bat`.

On another computer install Python 3.10+ and run `install_engine_windows.ps1`
from PowerShell. It downloads the official pinned portable archive and checks
its SHA-256 before unpacking. Alternatively install SatDump yourself and set
`lrpt.satdump` to its CLI executable. Keep all its resources, pipelines and DLLs.
Then run `start_windows.bat`, which creates the plugin's Python environment.
The engine is independently licensed GPL-3.0; upstream license/source are in
the [SatDump repository](https://github.com/SatDump/SatDump).

## Linux

Install Python venv support, NumPy/PyYAML through the launcher, librtlsdr and
SatDump. Use an official package matching your distribution or the upstream
[build/install instructions](https://docs.satdump.org/). The Windows vendor
directory is ignored on Linux; `satdump` is discovered on PATH or by
`lrpt.satdump` / `BQE_SATDUMP`.

```sh
sudo apt install python3-venv librtlsdr0 rtl-sdr
sh plugins/bqe_lrpt_decoder/start_linux.sh
```

Install the distribution's RTL-SDR udev rules and ensure its DVB driver is not
claiming the dongle. Choose `cli_version: '1'` for SatDump 1.x; choose `'2'` for
2.x, whose command includes `pipeline`. Windows 1.2.2 is the tested engine;
2.x command construction is covered by tests, but the 2.x binary is unverified.

## Use during BQE satellite passes

The relevant satellite entry needs:

```yaml
decode_lrpt_images: true
lrpt:
  satellite: M2-4
  symbol_rate: 72000
```

See `example_satellites.yaml` for complete M2-3 and M2-4 profiles. Use the
satellite's actual downlink frequency. BQE selects the LRPT receiver instead
of the audio SDR receiver, skips CAT commands, and stops IQ capture at pass end.
Leave SSTV/telemetry/SSDV flags disabled in an LRPT entry. The same dongle cannot
be opened simultaneously by another receiver. Idle presets also accept this flag.
Restart BQE after installing code changes.

Station defaults live under `lrpt` in `bqe_config/my_rig.yaml`; per-satellite
`lrpt` values override them. RF identity/gain/PPM/USB-library defaults come from
`sdr`. Audio gain, audio bandwidth and audio recording options do not apply.
The default 240 kS/s IQ rate covers 72/80 ksym/s LRPT. Capture is centered on the
configured downlink with no digital/audio filter or FM demodulation; SatDump
performs carrier/timing recovery. BQE does not retune this capture for Doppler.

`duration` is a safety limit (default 1200 s, maximum 3600 s); the pass may stop
sooner. A 20-minute recording at 240 kS/s uses about 576 MB; at 960 kS/s it uses
2.3 GB. The configured free-space reserve and estimated capture size are checked.
No recordings are automatically deleted. Decoder workers have a configurable
timeout and release the SDR before processing, allowing subsequent passes.

## Controls, gallery and replay

The launcher opens `http://127.0.0.1:8773`. Select the satellite/frequency/rate,
click **Start IQ capture**, and **Stop & decode** when finished. The gallery
updates while SatDump runs. A clean decoder exit with no images is labelled
`no_images`, not successful reception. RS checking stays enabled; missing lines
are not synthesized. All imagery is generated by SatDump from received data.

Close the standalone server (Ctrl+C in its terminal) before a managed pass:
closing just the browser tab does not close the server or release its port.
After a managed pass, use the launcher to view its completed results. Port 8773
is independent of the audio SDR control port 8772.

Recordings are under `plugins/bqe_lrpt_decoder/recordings/<UTC-time>-<id>/`:
`capture.sigmf-data`, `capture.sigmf-meta`, `job.json`, `satdump.log`, and
`products/`. Jobs survive restarts. Replay creates a new job referencing the
original IQ without modifying it. IQ must be centered on the chosen downlink;
quarter-rate-offset BQE SDR captures need an explicit frequency-shift conversion
before replay. Raw WAV/speaker recordings are not accepted.

## Validation and limits

```sh
python -m unittest discover -s plugins/bqe_lrpt_decoder/tests -v
```

13 tests pass on Windows/Python 3.12 and Linux/Python 3.8, covering exact IQ
preservation, metadata, both pipeline rates, CLI generations, replay validation,
USB failure, disk reserve, worker outcomes, HTTP origin/path restrictions and
exclusive tracker routing. The real Windows SatDump 1.2.2 engine processed a
synthetic noise capture through both 72 and 80 ksym/s pipelines and correctly
reported no images. A direct USB attempt
correctly reported the dongle busy while the user's BQE SDR was running; that
receiver was left undisturbed. Actual LRPT image recovery and the Linux SatDump
binary still require validation with a satellite signal or known-good recording.

References: [SatDump pipeline parameters](https://github.com/SatDump/SatDump.Docs/blob/master/pipelines.rst),
[official releases](https://github.com/SatDump/SatDump/releases),
[NOAA POES retirement](https://ospo.noaa.gov/operations/poes/).
