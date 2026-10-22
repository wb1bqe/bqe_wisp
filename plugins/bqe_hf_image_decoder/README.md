# HF digital SSTV receiver

Receive HF SSDV using the original xssfox **FreeDV-TNC 700D** framing, or
experimental **HamDRM / EasyPal** images. Auto mode feeds the same audio to both
receivers continuously; validated data identifies the format, including when
the transmitting format changes. This is a receive-only plugin.

## Use in BQE

Restart BQE after installing the plugin. Select the `hf_digital_sstv` preset,
or add these settings to your own preset:

```yaml
decode_hf_images: true
hf_image_profile: auto  # auto, freedv, or hamdrm
```

The receiver uses `radio_soundcard` in `bqe_config/my_rig.yaml`, or the SDR audio
bridge when the radio is configured as an SDR. It starts after preset tuning,
opens its controls, and stops when another preset is selected, a satellite pass
starts, or BQE exits. It also works as the configured idle preset, without a
`program_to_run_while_waiting` command. `decode_sstv_images` remains the separate
analog SSTV setting.

The included 7.058 MHz LSB preset is an example. Set frequency and sideband for
your intended signal. Keep squelch open and select the channel carrying radio
audio. The control page shows waveform, waterfall, detected mode, and saved
images. JPEGs and accompanying JSON metadata go to the configured SSTV Gallery.

## Standalone controls and files

On Windows, run `start_windows.bat`; dependencies are installed into the plugin's
own `.venv`. Windows x64 native libraries are included. Open
<http://127.0.0.1:8776>, choose an input and start reception, or upload MP3/WAV.
Launchers open idle controls and do not start recording until requested.

For a headless file decode, from the project directory:

```powershell
plugins\bqe_hf_image_decoder\.venv\Scripts\python.exe plugins\bqe_hf_image_decoder\bqe_hf_image_decoder.py --file recording.wav --no-web
```

Use `--profile freedv` or `--profile hamdrm` to restrict reception, and `--output`
to override the gallery directory. Managed BQE launches use a free fallback port
if 8776 is occupied.

On Linux, install Python venv support, a C compiler, make, Codec2 (`libcodec2`),
FFTW development files (`libfftw3-dev`), and the system audio dependencies used
by SoundCard. Run `sh start_linux.sh`. It builds HamDRM and the shared SSDV
renderer if needed. Linux runtime operation has not been verified here.

## Compatibility and validation

- FreeDV 700D: generated native Codec2 audio decodes into a complete SSDV image
  at 44.1 and 48 kHz, using arbitrary input chunk boundaries.
- Framing checks cover CRC rejection, one lost frame recovery, and rejection
  of two lost frames. Noise does not create images.
- HamDRM: native receiver startup/shutdown, gzip images, and all four RS levels
  are checked. Full over-the-air HamDRM/EasyPal reception still needs an
  independent recording test and is **experimental**. Hybrid transfers that
  require downloading images from the internet are not supported.
- This does not decode every modem that can carry SSDV. FreeDV-TNC2 data modes
  and FreeDV 700E are not supported. Codec2's current raw-data API omits 700E
  dispatch; it is deliberately excluded.
- Switching presets and pass startup are covered by lifecycle tests, including
  a pass arriving during preset tuning. Capture failures close native workers.

Run the development suite from the project root:

```powershell
plugins\bqe_hf_image_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_hf_image_decoder/tests -v
```

Help > Environment Diagnostics also includes two offline HF framing/object
self-tests. These do not access the radio or start native processes.

Protocol references: [HF SSDV](https://github.com/darksidelemm/hfssdv),
[FreeDV-TNC](https://github.com/xssfox/freedv-tnc), and
[Codec2](https://github.com/drowe67/codec2).
See `native/README.md` for native source and build details.
