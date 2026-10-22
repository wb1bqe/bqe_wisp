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

from plugins import settings_backup as admin
import bqe_wisp_web as web


class SettingsBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "bqe_config"
        self.config.mkdir()
        (self.config / "my_qth.yaml").write_bytes(b"my_callsign: TEST\r\n")
        (self.config / "nested").mkdir()
        (self.config / "nested" / "binary").write_bytes(bytes(range(256)))
        (self.config / "empty").mkdir()
        self.destination = self.root / "Backups with spaces"
        self.destination.mkdir()
        self.credential = self.root / admin.APRS_PRIVATE
        self.credential.parent.mkdir(parents=True)
        self.credential.write_text('{"passcode":"12345","enabled":false}')

    def archive(self, entries):
        path = self.destination / "selected.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries:
                info = zipfile.ZipInfo("placeholder")
                # Preserve hostile separators instead of Windows ZipInfo normalization.
                info.filename = name
                archive.writestr(info, data)
        return path

    def test_round_trip_entire_config_and_aprs_credential(self):
        result = admin.backup(self.config, self.destination)
        path = Path(result["path"])
        self.assertRegex(path.name, r"bqe_config_\d{8}_\d{6}_\d{6}\.zip")
        with zipfile.ZipFile(path) as archive:
            self.assertIn("bqe_config/empty/", archive.namelist())
            self.assertEqual(archive.read(admin.APRS_PRIVATE), self.credential.read_bytes())
        (self.config / "my_qth.yaml").write_text("changed")
        (self.config / "nested" / "binary").unlink()
        (self.config / "extra").write_text("keep")
        self.credential.write_text("changed")
        result = admin.restore(self.config, path)
        self.assertEqual(result["files"], 3)
        self.assertEqual((self.config / "my_qth.yaml").read_bytes(), b"my_callsign: TEST\r\n")
        self.assertEqual((self.config / "nested" / "binary").read_bytes(), bytes(range(256)))
        self.assertEqual((self.config / "extra").read_text(), "keep")
        self.assertEqual(json.loads(self.credential.read_text())["passcode"], "12345")
        self.assertNotIn("12345", json.dumps(result))

    def test_config_only_backup_keeps_existing_credential(self):
        path = self.archive([("bqe_config/my_qth.yaml", "restored")])
        before = self.credential.read_bytes()
        admin.restore(self.config, path)
        self.assertEqual(self.credential.read_bytes(), before)

    def test_backup_without_credential_and_unique_names(self):
        self.credential.unlink()
        first = admin.backup(self.config, self.destination)["path"]
        second = admin.backup(self.config, self.destination)["path"]
        self.assertNotEqual(first, second)
        with zipfile.ZipFile(first) as archive:
            self.assertNotIn(admin.APRS_PRIVATE, archive.namelist())
        with self.assertRaises(ValueError):
            admin.backup(self.config, self.config / "nested")

    def test_invalid_paths_leave_settings_unchanged(self):
        before = (self.config / "my_qth.yaml").read_bytes()
        for name in ("../escape", "/absolute", "bqe_config/../escape",
                     "bqe_config/C:evil", "bqe_config\\evil", "bqe_config/CON.txt",
                     "plugins/other.py", "bqe_config/trailing."):
            with self.subTest(name=name):
                path = self.archive([("bqe_config/my_qth.yaml", "changed"), (name, "bad")])
                with self.assertRaises(ValueError):
                    admin.restore(self.config, path)
                self.assertEqual((self.config / "my_qth.yaml").read_bytes(), before)

    def test_duplicate_conflict_and_size_rejected_before_writes(self):
        for entries in (
            [("bqe_config/Foo", "a"), ("bqe_config/foo", "b")],
            [("bqe_config/my_qth.yaml/", "")],
            [("bqe_config/item", "a"), ("bqe_config/item/child", "b")],
        ):
            with self.assertRaises((OSError, ValueError)):
                admin.restore(self.config, self.archive(entries))
        with patch.object(admin, "MAX_BYTES", 1), self.assertRaises(ValueError):
            admin.restore(self.config, self.archive([("bqe_config/new", "large")]))
        self.assertFalse((self.config / "new").exists())

    def test_write_failure_rolls_back_config_and_credential(self):
        path = self.archive([("bqe_config/my_qth.yaml", "changed"),
                             (admin.APRS_PRIVATE, "new")])
        original = admin.shutil.copyfile
        def copy(source, destination, *args, **kwargs):
            if Path(destination) == self.credential and "new" in Path(source).parts:
                raise PermissionError("simulated write failure")
            return original(source, destination, *args, **kwargs)
        with patch.object(admin.shutil, "copyfile", side_effect=copy):
            with self.assertRaises(PermissionError):
                admin.restore(self.config, path)
        self.assertEqual((self.config / "my_qth.yaml").read_bytes(), b"my_callsign: TEST\r\n")
        self.assertEqual(json.loads(self.credential.read_text())["passcode"], "12345")

    def test_browse_filters_and_handles_spaces(self):
        (self.destination / "ignore.txt").write_text("test")
        self.archive([("bqe_config/a", "a")])
        result = admin.browse(str(self.root))
        self.assertIn("Backups with spaces", [e["name"] for e in result["entries"]])
        self.assertEqual([e["name"] for e in admin.browse(str(self.destination))["entries"]],
                         ["selected.zip"])

    def test_http_menu_wizard_backup_restore_and_access(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0),
                                     partial(web.WebConsoleHandler, status_payload_func=lambda: {}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def post(payload, headers=None):
            return urlopen(Request(base + "/api/admin/settings", data=json.dumps(payload).encode(),
                                   headers={"Content-Type": "application/json", **(headers or {})}))
        try:
            with patch.object(web, "SCRIPT_DIR", self.root):
                with urlopen(base + "/admin/settings?mode=backup") as response:
                    page = response.read().decode()
                    self.assertIn("APRS-IS credential", page)
                with post(dict(action="backup", path=str(self.destination))) as response:
                    backup = json.load(response)
                with post(dict(action="restore", path=backup["path"])) as response:
                    self.assertEqual(json.load(response)["files"], 3)
                for headers in ({"Origin": "https://elsewhere.invalid"}, {"Host": "evil.invalid"}):
                    with self.assertRaises(HTTPError) as error:
                        post(dict(action="browse"), headers)
                    self.assertEqual(error.exception.code, 403)
                with patch.object(web.WebConsoleHandler, "_is_local_config_request", return_value=False):
                    with self.assertRaises(HTTPError) as error:
                        post(dict(action="browse"))
                    self.assertEqual(error.exception.code, 403)
                with self.assertRaises(HTTPError) as error:
                    post(dict(action="restore", path=str(self.root / "missing.zip")))
                self.assertEqual(error.exception.code, 400)
            source = Path(web.__file__).read_text(encoding="utf-8")
            self.assertIn('id="backupSettingsMenuItem"', source)
            self.assertIn('id="restoreSettingsMenuItem"', source)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
