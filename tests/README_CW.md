# CW decoder regression checks

Run `python -m unittest discover -s tests -p test_cw_decoder.py` with Node.js
on PATH. The test loads the actual rendered JavaScript from
`build_recordings_html` without importing the application or using audio hardware.
No new application dependencies are required.

The deterministic tests cover 12, 20, and 35 WPM; upward and downward pitch
steps during keyed elements; drift; fading and changing noise; 44.1/48 kHz
input; stereo; 3100 Hz tones; strong continuous retuning birdies (including
one 500 Hz from the wanted tone); and rejection of silence, noise, continuous
carriers, retuning birdies alone, and empty audio. Both exact text and speed
are checked. The test text is independent of the supplied satellite callsign.

## SDR input filtering (2026-10-03)

Before reducing the sample rate, the browser decoder now applies a symmetric
Blackman-windowed sinc low-pass filter to each channel separately. Previously,
sample skipping folded high-frequency SDR interference into the CW band.
The filtered audio is reused for both analysis windows; no dependencies or
receiver configuration changes are required. Existing 8 kHz audio bypasses
the sample-rate reduction.

The adaptive keying threshold is capped at 25 dB above nearby noise so very
clean tones do not suppress valid dots at other pitches during retuning.
Short-window results with a signal/timing score below 85 also try the longer
window, retaining the result with the higher score. This is a heuristic for
signal detection, not a measure of transcript accuracy.

All 27 synthetic cases pass, including 44.1/48/96 kHz SDR interference,
35 WPM retunes, opposite-polarity stereo, a 3400 Hz wanted tone, and rejection
of high-frequency interference without CW. The local LASARSAT capture
`LASARSAT-beacon-20260927T114204Z-a7e9f5b7.wav` passes the existing phrase,
pulse-count, speed, and progress assertions, recovering
`DE OK0LSR = 73 FROM LASARSAT AR` at 20.1 WPM. Its numerical telemetry remains
unverified. Full-file processing in the local Node harness increased from
about 2.1 to 2.9 seconds, including WAV loading.

Four additional captures were compared before and after: LASARSAT at
2026-09-27 13:19:15 UTC, MARINA at 2026-10-03 15:59:53 UTC, RS-44 at
2026-10-03 09:13:53 UTC, and FO-29 at 2026-10-02 23:33:59 UTC. Both versions
reported no clearly keyed tone in all four. These checks establish the
aliasing fix and preserve the existing radio regression; they do not establish
successful decoding of those recent captures or a general character-error-rate
improvement. Local comparison output is in
`logs/cw-sdr-20261003/comparison.json` (recordings are not test fixtures in Git).

## Decoder changes

- Search approximately 120–3400 Hz independently in each frame, using a
  shorter analysis window and 8 ms hops for normal-length recordings.
- Identify persistent unkeyed spectral tones before selecting a signal;
  exclude their energy from the nearby noise estimate as well. Suppress
  window leakage around their retunes.
- Maintain a separate per-frequency noise history for strong, abrupt tuning
  steps that temporarily spread the signal across the spectrum.
- Adapt keying thresholds locally, retain short dots, and fit both Morse
  timing and the mark/space duration bias introduced by detection.
- Label the displayed signal/timing score explicitly as **not text accuracy**.

## Real recording validation (2026-09-24)

Both supplied MP3s were decoded to native 48 kHz mono PCM and passed to the
old and revised browser decoding functions. Durations were 642.768 and
657.696 seconds. The supplied transcripts were used only for evaluation;
they are not embedded in the decoder or used to correct its output.

The revised decoder recovers `DE OK0LSR` in both recordings and preserves
`DE OK0LSR = 73 FROM LASARSAT AR` in the second. `=` is the existing display
for the BT prosign. The numerical telemetry still contains substantial
errors in both versions. For example, the first reference payload
`U924425R4T15PR` becomes `U924I25R4T15PIW` in the revision. The second
recording's weak telemetry prefix is also not reliably recovered. Results
are mixed on these real recordings; passing synthetic tests does not
establish a general improvement in real-world character error rate.

The revised signal-processing run took approximately 0.9 seconds per file
in the local Node.js harness, versus approximately 0.45 seconds previously.
These figures exclude MP3 decoding and browser scheduling/yields. The extra
work is for interference rejection and finer timing, not a speedup.

## Limits

Audio lost outside the receiver passband cannot be reconstructed reliably.
Deep fades, overlapping tones, speech, and multiple keyed signals can still
produce false characters. Carrier rejection assumes an unkeyed tone persists
for at least 650 ms at a given spectral location; more rapid retuning and
very slow CW need additional validation. Extremely long files hit the frame
cap and consequently have coarser timing. A high signal/timing score must not
be interpreted as verified telemetry.
