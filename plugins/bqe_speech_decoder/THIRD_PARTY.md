# Speech dependencies

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — MIT.
- [CTranslate2](https://github.com/OpenNMT/CTranslate2) — MIT inference engine.
- [OpenAI Whisper](https://github.com/openai/whisper) — MIT model weights/code;
  the default converted model is [Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en).
- [Silero VAD](https://github.com/snakers4/silero-vad) — MIT; speech detection via faster-whisper/ONNX Runtime.
- [PyAV](https://github.com/PyAV-Org/PyAV) — BSD-3-Clause; its binary distributions
  include FFmpeg libraries with their own licensing information.

Dependencies install through pip, and models download separately. The source
release does not redistribute virtual environments, model weights or GPU runtimes.
Consult each installed distribution for its full notices and licenses.
