"""Exercise Windows first-run extraction with a tiny, real ZIP payload."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from standalone_location import choose_destination, record_path, remember_destination

if os.name == "nt":
    from packaging_windows import launcher


@unittest.skipUnless(os.name == "nt", "Windows extraction lock required")
class WindowsInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.parent = self.root / "Existing parent"
        self.parent.mkdir()
        self.sibling = self.parent / "other app"
        self.sibling.mkdir()
        (self.sibling / "keep.bin").write_bytes(b"\x00keep\xff")
        (self.parent / "notes.txt").write_bytes(b"keep notes\r\n")
        self.files = {"bqe_wisp/bqe_config/my_qth.yaml": b"test config",
                      "bqe_wisp/bqe_wisp.py": b"# application",
                      "_python/python.exe": b"fake runtime"}
        with zipfile.ZipFile(self.bundle / "payload.zip", "w") as archive:
            for name, content in self.files.items():
                archive.writestr(name, content)
        self.metadata = dict(payload_sha256=launcher.sha256(self.bundle / "payload.zip"),
                             build_id="test-build", files={
                                 name: hashlib.sha256(content).hexdigest()
                                 for name, content in self.files.items()})
        (self.bundle / "bundle.json").write_text(json.dumps(self.metadata))
        self.default = self.root / "local data" / "BQE_Wisp" / "test-build"

    def choose(self):
        return choose_destination(self.parent, self.default, self.metadata, allow_nonempty_parent=True)

    def assert_siblings_preserved(self):
        self.assertEqual((self.sibling / "keep.bin").read_bytes(), b"\x00keep\xff")
        self.assertEqual((self.parent / "notes.txt").read_bytes(), b"keep notes\r\n")

    def test_windows_first_run_default_is_c_bqe(self):
        self.assertEqual(launcher.FIRST_RUN_DIRECTORY, Path("C:/bqe"))

    def test_no_dir_installs_then_reuses_metadata(self):
        # Substitute a disposable parent: never install into the real C:/bqe in tests.
        with patch.object(launcher, "__file__", str(self.bundle / "launcher.py")), \
                patch.object(launcher.sys, "argv", ["BQE_Wisp.exe", "--extract-only"]), \
                patch.object(launcher, "FIRST_RUN_DIRECTORY", self.parent), \
                patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local data")}):
            self.assertEqual(launcher.main(), 0)
            record = record_path(self.default, self.metadata)
            self.assertEqual(Path(json.loads(record.read_text())["directory"]), self.parent)
            settings = self.parent / "bqe_wisp/bqe_config/my_qth.yaml"
            settings.write_bytes(b"saved user settings")
            unused = self.root / "different default"
            with patch.object(launcher, "FIRST_RUN_DIRECTORY", unused):
                self.assertEqual(launcher.main(), 0)
            self.assertFalse(unused.exists())
            self.assertEqual(settings.read_bytes(), b"saved user settings")
            self.assert_siblings_preserved()

    def test_explicit_dir_overrides_first_run_default(self):
        unused = self.root / "unused default"
        self.assertEqual(choose_destination(self.parent, self.default, self.metadata,
                         allow_nonempty_parent=True, first_run_destination=unused), self.parent)
        self.assertFalse(unused.exists())

    def test_default_conflicting_application_shows_rename_message(self):
        (self.parent / "bqe_wisp").mkdir()
        with self.assertRaisesRegex(ValueError, "Rename the old folder"):
            choose_destination(None, self.default, self.metadata,
                               allow_nonempty_parent=True, first_run_destination=self.parent)
        self.assertFalse(record_path(self.default, self.metadata).exists())
        self.assert_siblings_preserved()

    def test_bad_saved_metadata_does_not_fall_back(self):
        record = record_path(self.default, self.metadata)
        record.parent.mkdir(parents=True)
        for contents in ('invalid json', json.dumps({"directory": str(self.root / "missing")})):
            record.write_text(contents)
            with self.assertRaisesRegex(ValueError, "Saved installation"):
                choose_destination(None, self.default, self.metadata,
                                   allow_nonempty_parent=True, first_run_destination=self.parent)
            self.assertFalse((self.parent / "bqe_wisp").exists())

    def test_nonempty_parent_extracts_both_payload_roots(self):
        destination = self.choose()
        launcher.unpack(self.bundle, destination, self.metadata)
        for name, content in self.files.items():
            self.assertEqual((self.parent / name).read_bytes(), content)
        self.assert_siblings_preserved()
        self.assertTrue((self.parent / ".bundle-ready.json").exists())

    def test_cli_dir_and_data_dir_accept_nonempty_parent(self):
        for option in ("--dir", "--data-dir"):
            with self.subTest(option=option), \
                    patch.object(launcher, "__file__", str(self.bundle / "launcher.py")), \
                    patch.object(launcher.sys, "argv", ["BQE_Wisp.exe", option, str(self.parent), "--extract-only"]), \
                    patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local data")}):
                self.assertEqual(launcher.main(), 0)
                self.assert_siblings_preserved()

    def test_existing_application_suggests_rename_without_changes(self):
        for as_file in (False, True):
            application = self.parent / "bqe_wisp"
            if as_file:
                application.write_bytes(b"existing file")
            else:
                application.mkdir()
            with self.assertRaisesRegex(ValueError, "Rename the old folder.*before rerunning"):
                self.choose()
            self.assert_siblings_preserved()
            self.assertFalse((self.parent / "_python").exists())
            if as_file:
                self.assertEqual(application.read_bytes(), b"existing file")
                application.unlink()
            else:
                application.rmdir()

    def test_existing_runtime_is_not_overwritten(self):
        runtime = self.parent / "_python"
        runtime.mkdir()
        (runtime / "keep").write_text("keep")
        with self.assertRaisesRegex(ValueError, "_python"):
            self.choose()
        self.assertEqual((runtime / "keep").read_text(), "keep")

    def test_conflict_appearing_after_selection_is_rechecked(self):
        destination = self.choose()
        (destination / "bqe_wisp").mkdir()
        with self.assertRaisesRegex(ValueError, "Rename the old folder"):
            launcher.unpack(self.bundle, destination, self.metadata)
        self.assertFalse((destination / "_python").exists())
        self.assert_siblings_preserved()

    def test_same_build_relaunch_preserves_user_settings(self):
        launcher.unpack(self.bundle, self.choose(), self.metadata)
        settings = self.parent / "bqe_wisp/bqe_config/my_qth.yaml"
        settings.write_bytes(b"user edits")
        launcher.unpack(self.bundle, self.choose(), self.metadata)
        self.assertEqual(settings.read_bytes(), b"user edits")
        self.assert_siblings_preserved()

    def test_pending_payload_must_match_and_can_retry(self):
        pending = self.parent / ".bundle-pending"
        pending.write_text("0" * 64)
        with self.assertRaises(ValueError):
            self.choose()
        with self.assertRaises(ValueError):
            launcher.unpack(self.bundle, self.parent, self.metadata)
        pending.write_text(self.metadata["payload_sha256"])
        (self.parent / "bqe_wisp").mkdir()
        launcher.unpack(self.bundle, self.choose(), self.metadata)
        self.assertFalse(pending.exists())
        self.assert_siblings_preserved()

    def test_other_build_explains_rename_and_preserves_files(self):
        (self.parent / "bqe_wisp").mkdir()
        (self.parent / ".bundle-ready.json").write_text(json.dumps({"payload_sha256": "0" * 64}))
        with self.assertRaisesRegex(ValueError, "Rename or move the old installation"):
            self.choose()
        self.assert_siblings_preserved()

    def test_archive_cannot_write_unrelated_parent_file(self):
        with zipfile.ZipFile(self.bundle / "payload.zip", "w") as archive:
            archive.writestr("notes.txt", "overwrite")
        self.metadata["payload_sha256"] = launcher.sha256(self.bundle / "payload.zip")
        with self.assertRaisesRegex(RuntimeError, "Invalid path"):
            launcher.unpack(self.bundle, self.choose(), self.metadata)
        self.assert_siblings_preserved()


if __name__ == "__main__":
    unittest.main()
