"""Run the rendered browser decoder against deterministic audio using Node.js.

Run: python -m unittest discover -s tests -p test_cw_decoder.py
No browser, audio device, MP3 codec, or application dependencies are needed.
"""
import ast
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CWDecoderTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript tests")
    def test_audio_regressions(self):
        # Read the actual Python string literal, including its JS escaping,
        # without importing the server or starting any hardware services.
        tree = ast.parse((ROOT / "bqe_wisp_web.py").read_text(encoding="utf-8-sig"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "build_recordings_html")
        page = max((node.value for node in ast.walk(function)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)), key=len)
        command = [shutil.which("node"), str(ROOT / "tests" / "cw_decoder_cases.cjs")]
        capture = (ROOT / "plugins" / "bqe_sound_recorder" / "recordings" /
                   "LASARSAT-beacon-20260927T114204Z-a7e9f5b7.wav")
        if capture.exists():
            command.append(str(capture))
        result = subprocess.run(
            command,
            input=page, text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
