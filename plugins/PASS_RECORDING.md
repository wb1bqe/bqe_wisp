# Pass audio recording

Set this at satellite-entry level in `bqe_config/satellites.yaml`:

```yaml
  - nickname: example
    # Keep the satellite's existing frequency, scheduling and decoder fields.
    record_audio: true
    program_to_run_during_pass: ''
```

The same entry works with either station configuration in `my_rig.yaml`:

- `radio_is_sdr: true`: subscribe to the active SDR's demodulated audio.
- Conventional radio: capture the input named by `radio_soundcard`.

No default microphone or speaker-loopback fallback is used. Missing or unavailable
inputs produce a recording warning. This does not change decoder selection.
Conventional-radio decoders still need appropriate audio (for example, flat
discriminator/data output for SO-127); selecting recording cannot restore signal
information lost by a radio's audio filtering.

Pass recordings are mono, 48 kHz, 16-bit PCM WAV on Windows and Linux. They use
`plugins.bqe_sound_recorder.recordings` in `general_settings.yaml`, with a satellite
nickname, UTC timestamp and unique suffix in the filename. The recordings browser
plays both WAV and existing MP3 files. Audio → Record Audio continues to create MP3.
The recorder closes and finalizes at pass end, or when its tracking process exits.

`record_audio: false` explicitly disables pass audio. The top-level boolean takes
precedence over `sdr.record_audio` and suppresses an old sound-recorder command so
that two recorders do not run for the same entry. Other pass programs remain intact
and can run alongside recording. If the top-level field is absent, legacy recorder
commands and SDR recording defaults continue to behave as before.

`sdr.record_iq` remains independent and uses the SDR's output configuration. LRPT
uses raw IQ rather than demodulated audio; use its existing LRPT capture settings,
not `record_audio: true` (which reports a warning on an LRPT pass).

The supplied satellite entries and templates have been migrated away from explicit
sound-recorder commands and SDR-only audio recording paths. Existing files in those
old paths are retained. Restart BQE between passes to load application changes.
# Decoder browser tabs

Decoder control pages attempt to close automatically after at least three failed
status requests spanning five seconds. Successful HTTP responses reset this grace
period; decoder errors and completed recordings do not close a reachable page.
Status requests time out after five seconds and polling requests do not overlap.
If the browser refuses to close a tab opened externally, the page unloads to
`about:blank` to release decoder content and stop polling. That empty tab can
still require manual closing. Restart or reload existing decoder pages to load
this behavior.

