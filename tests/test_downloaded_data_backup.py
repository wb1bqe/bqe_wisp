import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import zipfile

from plugins import downloaded_data_backup as data
import bqe_wisp_web as web


class DownloadedDataBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "app"
        self.root.mkdir()
        self.destination = Path(temporary.name) / "Backups"
        self.destination.mkdir()
        self.files = {
            "sstv_gallery/image.png": b"image",
            "sstv_gallery/nested/metadata.json": b"{}",
            "sstv_gallery/.hidden": b"sidecar",
            "plugins/bqe_aprs_decoder/decoded/aprs.jsonl": b'{"frame_hex":"12"}\n',
            "plugins/bqe_tlm_decoder/decoded/pass.kiss": b"\x00\xff",
            "plugins/bqe_so127_decoder/decoded/nested/raw.bin": b"packet",
            "decoded/other.txt": b"other",
            "plugins/bqe_sound_recorder/recordings/pass.mp3": b"audio",
            "plugins/bqe_sound_recorder/recordings/pass.mp3.transcript.json": b"{}",
            "plugins/bqe_sound_recorder/recordings/pass.mp3.transcript.txt": b"speech",
            "plugins/bqe_sdr/recordings/pass.wav": b"wave",
            "plugins/bqe_sdr/recordings/pass.sigmf-data": b"iq",
            "plugins/bqe_sdr/recordings/pass.sigmf-meta": b"{}",
            "plugins/bqe_aprs_decoder/upload_receipts/receipt.json": b'{"uploaded_at":"2026-10-22"}',
        }
        self.excluded = {
            "plugins/bqe_lrpt_decoder/recordings/pass/products/weather.png": b"weather",
            "plugins/bqe_lrpt_decoder/decoded/weather.dat": b"weather",
            "plugins/bqe_aprs_decoder/aprs-is.private.json": b'{"passcode":"12345"}',
            "bqe_config/my_qth.yaml": b"configuration",
            "plugins/bqe_speech_decoder/models/model.bin": b"model",
            "plugins/bqe_pass_voice/cache/announcement.wav": b"cache",
            "plugins/bqe_aprs_decoder/.uploads/temporary.wav": b"temporary",
        }
        for name, content in {**self.files, **self.excluded}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.root / "sstv_gallery/empty").mkdir()

    def zip(self, entries):
        path = self.destination / "input.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries:
                info = zipfile.ZipInfo("placeholder")
                info.filename = name
                archive.writestr(info, content)
        return path

    def test_all_data_roundtrip_excludes_weather_settings_and_credentials(self):
        result = data.backup_data(self.root, self.destination)
        self.assertRegex(Path(result["path"]).name, r"bqe_downloaded_data_\d{8}_\d{6}_\d{6}\.zip")
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
            for name, content in self.files.items():
                self.assertEqual(archive.read(name), content)
            self.assertIn("sstv_gallery/empty/", names)
            for name in self.excluded:
                self.assertNotIn(name, names)
        for name in self.files:
            (self.root / name).unlink()
        for name in self.excluded:
            (self.root / name).write_bytes(b"keep current")
        (self.root / "sstv_gallery/extra").write_text("keep")
        restored = data.restore_data(self.root, result["path"])
        self.assertEqual(restored["files"], len(self.files))
        for name, content in self.files.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        for name in self.excluded:
            self.assertEqual((self.root / name).read_bytes(), b"keep current")
        self.assertEqual((self.root / "sstv_gallery/extra").read_text(), "keep")

    def test_restore_overwrites_and_recreates_missing_decoded_folder(self):
        path = self.zip([("plugins/bqe_tlm_decoder/decoded/recovered.bin", "new"),
                         ("sstv_gallery/image.png", "restored")])
        (self.root / "plugins/bqe_tlm_decoder/decoded/pass.kiss").unlink()
        (self.root / "plugins/bqe_tlm_decoder/decoded").rmdir()
        data.restore_data(self.root, path)
        self.assertEqual((self.root / "sstv_gallery/image.png").read_text(), "restored")
        self.assertEqual((self.root / "plugins/bqe_tlm_decoder/decoded/recovered.bin").read_text(), "new")

    def test_custom_gallery_restores_to_current_configuration(self):
        old = self.destination.parent / "custom gallery"
        old.mkdir()
        (old / "image.jpg").write_bytes(b"custom")
        archive = data.backup_data(self.root, self.destination, str(old))["path"]
        new = self.destination.parent / "new gallery"
        data.restore_data(self.root, archive, str(new))
        self.assertEqual((new / "image.jpg").read_bytes(), b"custom")
        self.assertTrue((self.root / "sstv_gallery/image.png").exists())

    def test_weather_or_program_overlap_is_rejected(self):
        for gallery in ("plugins/bqe_lrpt_decoder/recordings", ".", "plugins"):
            with self.subTest(gallery=gallery), self.assertRaises(ValueError):
                data.backup_data(self.root, self.destination, gallery)
        with self.assertRaises(ValueError):
            data.backup_data(self.root, self.root / "sstv_gallery")

    def test_custom_recordings_restore_to_current_location(self):
        old = self.destination.parent / 'old recordings'
        old.mkdir()
        (old / 'pass.mp3').write_bytes(b'custom audio')
        (old / 'pass.mp3.transcript.txt').write_text('transcript')
        archive = data.backup_data(self.root, self.destination, recordings=str(old))['path']
        new = self.destination.parent / 'new recordings'
        data.restore_data(self.root, archive, recordings=str(new))
        self.assertEqual((new / 'pass.mp3').read_bytes(), b'custom audio')
        self.assertEqual((new / 'pass.mp3.transcript.txt').read_text(), 'transcript')
        with self.assertRaises(ValueError):
            data.backup_data(self.root, old, recordings=str(old))
        for folder in ('.', 'plugins', 'plugins/bqe_sound_recorder',
                       'bqe_config', 'plugins/bqe_lrpt_decoder/recordings'):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                data.data_roots(self.root, recordings=folder)

    def test_recordings_restore_when_plugin_folder_is_absent(self):
        empty = self.destination / 'fresh app'
        empty.mkdir()
        archive = self.zip([('plugins/bqe_sound_recorder/recordings/pass.mp3', b'audio'),
                            ('plugins/bqe_aprs_decoder/upload_receipts/receipt.json', b'{}')])
        data.restore_data(empty, archive)
        self.assertEqual((empty / 'plugins/bqe_sound_recorder/recordings/pass.mp3').read_bytes(), b'audio')
        self.assertTrue((empty / 'plugins/bqe_aprs_decoder/upload_receipts/receipt.json').exists())

    def test_hostile_or_out_of_scope_zip_does_not_change_data(self):
        invalid = ["plugins/bqe_lrpt_decoder/decoded/weather.dat",
                   "plugins/bqe_lrpt_decoder/recordings/new.bin",
                   "bqe_config/my_qth.yaml", "plugins/bqe_aprs_decoder/aprs-is.private.json",
                   "../escape", "sstv_gallery/../escape", "sstv_gallery\\evil",
                   "sstv_gallery/C:evil", "sstv_gallery/CON", "sstv_gallery"]
        for name in invalid:
            with self.subTest(name=name):
                archive = self.zip([("sstv_gallery/image.png", b"changed"), (name, b"bad")])
                with self.assertRaises(ValueError):
                    data.restore_data(self.root, archive)
                self.assertEqual((self.root / "sstv_gallery/image.png").read_bytes(), b"image")

    def test_write_failure_rolls_back_data(self):
        archive = self.zip([("sstv_gallery/image.png", b"changed"),
                            ("plugins/bqe_aprs_decoder/decoded/new.jsonl", b"new")])
        copy = data.shutil.copyfile
        def failed(source, target, *args, **kwargs):
            if Path(target).name == "new.jsonl" and "new" in Path(source).parts:
                raise PermissionError("write failure")
            return copy(source, target, *args, **kwargs)
        with patch.object(data.shutil, "copyfile", side_effect=failed), self.assertRaises(PermissionError):
            data.restore_data(self.root, archive)
        self.assertEqual((self.root / "sstv_gallery/image.png").read_bytes(), b"image")
        self.assertFalse((self.root / "plugins/bqe_aprs_decoder/decoded/new.jsonl").exists())

    def test_empty_source_reports_no_data(self):
        empty = self.destination / "empty app"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "No SSTV"):
            data.backup_data(empty, self.destination)

    def test_new_decoder_folders_are_discovered(self):
        path = self.root / "plugins/bqe_future_decoder/decoded/deep/file"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"future")
        archive = data.backup_data(self.root, self.destination)["path"]
        with zipfile.ZipFile(archive) as saved:
            self.assertEqual(saved.read(path.relative_to(self.root).as_posix()), b"future")

    def test_http_menu_wizard_and_access(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0),
                                     partial(web.WebConsoleHandler, status_payload_func=lambda: {}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def post(payload, headers=None):
            return urlopen(Request(base + "/api/admin/downloaded-data",
                                   data=json.dumps(payload).encode(),
                                   headers={"Content-Type": "application/json", **(headers or {})}))
        try:
            with patch.object(web, "SCRIPT_DIR", self.root):
                with urlopen(base + "/admin/downloaded-data?mode=restore") as response:
                    self.assertIn("LRPT weather downloads are excluded", response.read().decode())
                with post(dict(action="backup", path=str(self.destination))) as response:
                    archive = json.load(response)["path"]
                with post(dict(action="restore", path=archive)) as response:
                    self.assertEqual(json.load(response)["files"], len(self.files))
                with self.assertRaises(HTTPError) as error:
                    post(dict(action="browse"), {"Origin": "https://other.invalid"})
                self.assertEqual(error.exception.code, 403)
                with patch.object(web.WebConsoleHandler, "_is_local_config_request", return_value=False):
                    with self.assertRaises(HTTPError) as error:
                        post(dict(action="browse"))
                    self.assertEqual(error.exception.code, 403)
            source = Path(web.__file__).read_text(encoding="utf-8")
            for label in ("Backup Downloaded Data", "Restore downloaded data"):
                self.assertIn(label, source)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
