# Multilingual radio transcription

Local Windows and Linux transcription of saved MP3/WAV recordings, including
ISS repeater passes. Uses faster-whisper, CPU int8, four threads, speech detection,
and amateur-radio vocabulary context for English, French, Spanish, Portuguese, Mandarin Chinese and Japanese.
No audio is uploaded, no API key is needed, and no live receiver is opened.

## Use in BQE WISP

On **View → Recordings**, check files, press one language button, and choose
**Transcribe speech**. The buttons are **English**, **French**, **Spanish**,
**Portuguese (Brazil)**, **Portuguese (Portugal)**, **Mandarin Chinese** and **Japanese**. English is selected initially.
Each batch uses only that chosen language; it does not try multiple languages.
Both Portuguese choices use Whisper's `pt` language with regional vocabulary
context. They are not separate accent models, and do not guarantee regional
spelling. Mandarin uses the model's `zh` language; Japanese uses `ja`. Both
preserve the original language's characters in saved transcripts. Neither needs
a separate download if `small` is already cached. Text stays in the spoken language; it is not translated into English.

The shared recording queue handles one file at a time, shows progress, and supports
cancellation. The first use downloads the selected model (roughly 500 MB each); subsequent use
can be completely offline. The default `auto` model setting selects `small.en`
for English and `small` for all other languages and auto-detection. One download
of `small` serves French, Spanish, both Portuguese choices, Mandarin Chinese and Japanese, with no separate
language modules. Additional Whisper languages remain available through the CLI.
Recognition quality varies by language. Startup may take longer while downloading/loading.

Choose **View transcript** beside a recording. Each timestamp replays the original
audio at that point. Download plain text from the viewer. Transcripts remain
available after restarting BQE, and are stored beside the original audio as
`recording.wav.transcript.json` and `recording.wav.transcript.txt` (likewise for MP3).
Re-transcribing replaces these transcript sidecars only. The audio is unchanged.
Cancellation/failure does not publish a new incomplete transcript. A changed
recording invalidates the saved transcript; wait until recording finishes and retry.

## Install and check

Use 64-bit Python 3.10+ and a platform supported by the upstream binary packages.
Windows x64 and Linux x86-64 are the primary tested targets. On Debian/Ubuntu,
install the matching `python3-venv` package if virtual-environment creation fails.
Python 3.8 (the stock Python in Ubuntu 20.04) is too old: use a newer Python.
If needed, set `BQE_SPEECH_PYTHON=/path/to/python3.12` when running the Linux launcher.
No microphone, audio server, or system FFmpeg installation is needed for files.

The main BQE installers include the plugin requirements. For an existing install,
these launchers create/check a separate plugin environment:

Windows, from the project directory:
```bat
plugins\bqe_speech_decoder\start_windows.bat --check
plugins\bqe_speech_decoder\start_windows.bat --download-model
plugins\bqe_speech_decoder\start_windows.bat --download-model --language es
plugins\bqe_speech_decoder\start_windows.bat --file "path\to\iss-rpt.wav" --offline
```

Linux, from the project directory:
```sh
bash plugins/bqe_speech_decoder/start_linux.sh --check
bash plugins/bqe_speech_decoder/start_linux.sh --download-model
bash plugins/bqe_speech_decoder/start_linux.sh --download-model --language pt-PT
bash plugins/bqe_speech_decoder/start_linux.sh --file "/path/to/iss-rpt.wav" --offline
```

`--check` checks dependencies without downloading or loading a model. For package
upgrades, run the plugin environment's Python with `-m pip install -r` and this
plugin's `requirements.txt`. PyAV is constrained to a compatible API version.
Help → Environment Diagnostics includes offline speech-plugin self-tests; these
use a simulated recognizer, not network access or model inference.

## Settings and GPU

Optional `settings.json` in this plugin directory:
```json
{"model":"auto","language":"en","device":"cpu","compute_type":"int8","cpu_threads":4,"offline":false}
```
`language` accepts a language code (for example `en`, `es`, `pt`, `fr`, `zh`, `ja`) or
`auto` (detect one language per recording); regional Portuguese accepts `pt-BR` or `pt-PT`. `--language` overrides it on the command line. The recordings page sends
its selected language for each batch. `--help` lists all accepted codes.
`model` accepts `auto`, a faster-whisper model name or an absolute path to a converted
model directory. `--model` overrides it for a CLI run. Models are cached under
this plugin's `models` directory. Set `offline` to true to forbid model downloads.
`base` is a smaller multilingual model; `.en` models only support English and
are rejected for other languages or auto-detection. Existing custom settings
using `small.en` should use `auto` to enable multilingual selection.
`base.en` is smaller/faster for English; larger models may help difficult audio but use more
memory/time. Test accuracy on your station's recordings before changing defaults.

Optional NVIDIA GPU use: set `device` to `cuda` and `compute_type` to `float16`
or `int8_float16`. Install the CUDA/cuDNN versions required by your installed
CTranslate2 version; see the [upstream GPU instructions](https://github.com/SYSTRAN/faster-whisper#gpu).
GPU drivers/libraries are not installed automatically. CPU operation is the default.

## Radio-specific accuracy

English context covers phonetic letters; English, French, Spanish, Portuguese, Mandarin Chinese and Japanese context
covers mixed letter/number callsigns,
two-/three-digit signal reports, and two-letter/two-digit grid squares.
Auto-detection and other languages use no language-specific prompt. The
transcript applies the known word corrections below; it does not turn phonetics
into a callsign, validate a station identity, or guess a locator. For example, a
spoken report might appear as “five nine” or “5-9”.

Fading, overlapping voices and squelch noise can still cause missed speech or
invented words. “Uncertain” is a heuristic based on the recognizer's scores, not
a calibrated confidence percentage; unmarked segments can also be wrong. Speech
detection can omit weak transmissions. Replay the audio before trusting callsigns,
reports or grid squares. Timestamps are recording offsets, not on-air UTC times.

## Phonetic word corrections

Every newly transcribed segment uses case-insensitive, whole-word regex
corrections: `killing` becomes `kilo`, and `husky` becomes `Whiskey`.
All occurrences are replaced, including next to punctuation, in every language
mode. Longer words are left intact. These radio-specific overrides apply even
when the recognizer meant the literal word. Both saved JSON and plain text
contain the corrected wording; timestamps and uncertainty flags are preserved.
Existing transcripts receive corrections when you re-transcribe the recording.

To add cases, edit `corrections.yaml` in this plugin directory:

```yaml
- pattern: '\bkilling\b'
  replacement: 'kilo'
- pattern: '\bhusky\b'
  replacement: 'Whiskey'
```

Append more entries in the same format. Use single quotes around regex patterns
so YAML preserves backslashes. Rules run in listed order, ignoring case, and
replacement capitalization is used exactly as written. The file is reloaded
before each recording, so no restart is needed. Use `[]` for no corrections.
Invalid YAML or regex rules stop transcription with an error before model loading
and leave existing transcripts intact. The plugin requirements include PyYAML.

## Developer checks

```sh
python -m unittest plugins.bqe_speech_decoder.tests.test_transcription
```
These tests use a fake recognizer and do not download models. Test real inference
separately with `--file` and an installed model.
