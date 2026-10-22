# BQE WISP browser audio streaming

## Install

1. Stop BQE WISP and back up your existing files.
2. Extract this ZIP into your BQE WISP installation directory, replacing `bqe_wisp.py` and `bqe_wisp_web.py`. Preserve the included `plugins/bqe_audio_stream/` directory structure. Your existing `bqe_track_continuously.py` needs no changes.
3. Install the additional package with the same Python interpreter used to run BQE WISP:

   Windows:
   ```bat
   python -m pip install sounddevice
   ```

   Linux:
   ```sh
   python3 -m pip install sounddevice
   ```
   Use your existing virtual environment if applicable. On Debian, Ubuntu, or Raspberry Pi OS, install the PortAudio runtime if needed:
   ```sh
   sudo apt install libportaudio2
   ```
4. Restart BQE WISP and reload the main browser page.

No FFmpeg or NumPy dependency is added by this feature. Existing BQE WISP dependencies still apply. Capture uses sounddevice's [RawInputStream API](https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html).

## Listen

### Recording filters and offline decoding

On **View → Recordings**, **Filter Satellites** opens a checkbox dialog using
the satellite/source prefixes in recording filenames. Files without a recognized
timestamp use **Other recordings**. Apply the filter to show the selected names;
hidden recordings are unchecked, and **Select all visible** selects only shown files.

Check up to 100 recordings and choose **Decode SSTV**, **Decode APRS**, or **Transcribe speech**.
Files decode one at a time, with progress, errors, and expandable results in the
table. **Cancel decoding** stops the batch. Images are saved to the configured
SSTV Gallery; APRS packet logs are saved by the APRS plugin, and decoded positions
also appear under **View → APRS Spots**. These offline jobs do not use or stop
the live receiver. The decoder plugins' usual dependencies must be installed.

Multilingual transcripts are saved beside the original audio as JSON and text. **View
transcript** opens timestamped text with click-to-replay audio and a text download.
Press a language button: English, French, Spanish, Portuguese (Brazil), Portuguese (Portugal), Mandarin Chinese, or Japanese.
Each batch transcribes only the selected language.
The local speech engine works on Windows and Linux; first use downloads a model.
Radio vocabulary context includes callsigns, signal reports and grid squares,
but weak/overlapping speech still needs verification. See
`plugins/bqe_speech_decoder/README.md` for setup and offline/GPU options.

### Live listening

Choose **Audio → Listen** for local radio monitoring. With **Radio is an SDR**
enabled, it subscribes to the running SDR's demodulated audio. Otherwise it
captures the input named by `radio_soundcard` in `bqe_config/my_rig.yaml`.
It never falls back to the default microphone or speaker loopback. Missing
inputs or a stopped SDR produce a message. Click **Stop Listening** to stop.
Playback uses the browser computer's default output; no recording is created.
Listen and Stream Audio are mutually exclusive within each browser tab.
Changing the rig source requires stopping and restarting Listen.

On Linux, conventional-radio listening uses SoundCard and a working PulseAudio
or compatible PipeWire audio service. SDR listening needs no sound-card input.

## Stream Audio

Choose **Audio → Stream Audio**. Audio from the input device on the computer running BQE WISP plays through the browser computer's speakers. The choice becomes **Stop Streaming**; click it to stop. Both Classic and Modern interfaces have the menu.

Each browser tab controls its own listening session. Closing or reloading a tab stops that session. Capture closes after the last listener disconnects (normally promptly; a failed connection can take several seconds to be detected). Shutdown and restart also close capture. Streaming does not automatically resume on page reload.

This captures the server's sound-card input, not the browser microphone or the server's speaker output. Select the radio input below if it is not the operating-system default. Anyone already able to access this BQE WISP web console can use the audio menu.

## Select the radio input

Edit `plugins/bqe_audio_stream/settings.json`:

```json
{
  "device": "USB Audio CODEC",
  "channel": 1,
  "sample_rate": null
}
```

- `device`: `null` uses the default input. Alternatively use a device index or a unique device-name substring. If several devices match, use an index from the list below.
- `channel`: one-based input channel; `1` is left/mono, `2` is right. Only the chosen channel is sent. If mono capture is unsupported, the plugin tries stereo capture.
- `sample_rate`: `null` uses the device's default rate. An explicit supported rate such as `48000` can be used.

List sound devices using the same interpreter:

```sh
python -m sounddevice
```

On Linux, substitute `python3` if appropriate. Settings are read when the first listener starts capture; stop all listeners before testing a change. JSON requires lowercase `null` and does not accept comments.

## Troubleshooting

Errors appear in the main console's message area. For a missing sounddevice module, run the installation command using the exact Python executable that launches BQE WISP. A device-open error can mean an unavailable input, unsupported rate, or another application holding the device exclusively.

If the stream starts but is silent, check the radio's USB audio routing, input gain, selected device/channel, browser tab mute, and browser computer's volume. On Windows, allow desktop applications to access microphone inputs. If simultaneous recording fails, check whether the audio driver supports shared capture.

PCM audio uses about 96 kB/s per listener at 48 kHz mono, plus transport overhead. Playback uses short buffers and drops excess queued audio to limit accumulated delay. Background-tab throttling or a poor network may cause gaps. If using a reverse proxy, disable buffering for `/audio-stream/pcm`.

## Implementation and validation

The plugin owns capture, bounded listener queues, device settings, and JavaScript playback. The main files only add the menu, HTTP routes, and shutdown hook. No tracking or tuning behavior is changed.

Validated Python syntax, JavaScript syntax, both menu themes, shared-capture lifecycle, bounded queues, stereo channel extraction, HTTP audio bytes, disconnect cleanup, rejected requests without the playback header, and device-error responses using a simulated sound device. Actual Windows/Linux sound-card capture and audible browser playback require testing on your installation.
