# Offline pass voice

BQE uses Piper 1.8.0 and the Cori medium British female voice for pass beginnings
and endings. Synthesis runs in a background worker and playback uses the system
default output. Set Windows' default playback device to speakers/headphones.

The root requirements install Piper. Alternatively, from the project root:

```powershell
python -m venv plugins/bqe_pass_voice/.venv
plugins/bqe_pass_voice/.venv/Scripts/python.exe -m pip install -r plugins/bqe_pass_voice/requirements.txt
```

BQE prefers this isolated interpreter when present; otherwise it uses its own
Python interpreter (including the private runtime in a standalone release).
Release builders must install the root requirements into that runtime.

In general_settings.yaml, under program_settings > bqe_wisp:

```yaml
pass_announcements_enabled: true
pass_announcements_windows_fallback: true
```

Restart BQE after changing settings. The second flag allows Windows System.Speech
to try an installed British female voice if Piper fails. Set it to false for
Piper only. Missing voices or playback failures log warnings; passes continue.
Linux uses paplay or aplay and has no Windows fallback.

## Voice assets and attribution

The included model and configuration come from
[rhasspy/piper-voices, en_GB/cori/medium](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_GB/cori/medium).
The unmodified upstream MODEL_CARD is included alongside them. It identifies
the training recordings as public-domain LibriVox material and credits the
[voice training project](https://brycebeattie.com/files/tts/).

[Piper](https://github.com/OHF-Voice/piper1-gpl) is GPL-3.0-or-later;
redistributions must retain its license notices and meet its source obligations.
The dataset statement in the model card describes the recordings, not a new
license grant from BQE. Preserve upstream model metadata when redistributing.
No cloud service or runtime model download is used.

Piper keeps up to `pass_announcements_cache_size: 20` recordings under
`plugins/bqe_pass_voice/cache`, reusing them across server restarts. Set this
nonnegative integer under `program_settings` > `bqe_wisp` in general_settings.yaml.
Set it to `0` to disable caching and clear cached recordings on the next
announcement. Restart BQE after changing it. The least recently used recordings
are removed first; changed voice model/config files cause fresh synthesis.
Beginning and ending messages each count as one recording.
