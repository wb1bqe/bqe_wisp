"""Target-side paths, remembered installation and preservation of user files."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from standalone_location import choose_destination, remember_destination, record_path

class LocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.default = self.root / 'App Data' / 'BQE_Wisp' / 'build-1'
        self.metadata = {'payload_sha256': 'a' * 64}

    def ready(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / '.bundle-ready.json').write_text(json.dumps(self.metadata))

    def test_full_path_is_remembered_only_after_extraction(self):
        destination = self.root / 'My Station Files'
        self.assertEqual(choose_destination(f'"{destination}"', self.default, self.metadata), destination)
        self.assertFalse(destination.exists())
        self.assertFalse(record_path(self.default, self.metadata).exists())
        with self.assertRaises(ValueError):
            remember_destination(destination, self.default, self.metadata)
        self.ready(destination)
        settings = destination / 'satellites.yaml'
        settings.write_bytes(b'# Existing settings\r\nrecord_audio: true\r\n')
        before = settings.read_bytes()
        remember_destination(destination, self.default, self.metadata)
        self.assertEqual(choose_destination(None, self.default, self.metadata), destination)
        self.assertEqual(settings.read_bytes(), before)

    def test_relative_path_resolves_on_target_from_launch_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            destination = choose_destination('My Station Files', self.default, self.metadata)
            self.assertEqual(destination, self.root / 'My Station Files')
            self.ready(destination)
            remember_destination(destination, self.default, self.metadata)
        finally:
            os.chdir(previous)
        self.assertEqual(choose_destination(None, self.default, self.metadata), destination)

    def test_runtime_dir_overrides_saved_choice(self):
        self.ready(self.default)
        remember_destination(self.default, self.default, self.metadata)
        other = self.root / 'Other Station'
        self.assertEqual(choose_destination(other, self.default, self.metadata), other)
        self.ready(other)
        remember_destination(other, self.default, self.metadata)
        self.assertEqual(choose_destination(None, self.default, self.metadata), other)

    def test_nonempty_folder_rejected_without_touching_files(self):
        folder = self.root / 'Existing Files'
        folder.mkdir()
        protected = folder / 'satellites.yaml'
        protected.write_text('unchanged')
        with self.assertRaisesRegex(ValueError, 'empty or new folder'):
            choose_destination(folder, self.default, self.metadata)
        self.assertEqual(protected.read_text(), 'unchanged')
        self.assertEqual(list(folder.iterdir()), [protected])

    def test_first_run_without_dir_extracts_nothing(self):
        with self.assertRaisesRegex(ValueError, '--dir'):
            choose_destination(None, self.default, self.metadata)
        self.assertFalse(self.default.exists())

    def test_existing_default_is_recognized(self):
        self.ready(self.default)
        self.assertEqual(choose_destination(None, self.default, self.metadata), self.default)

    def test_lost_saved_installation_is_not_silently_recreated(self):
        self.ready(self.default)
        remember_destination(self.default, self.default, self.metadata)
        (self.default / '.bundle-ready.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Saved installation'):
            choose_destination(None, self.default, self.metadata)

    def test_wrong_build_and_interrupted_extraction(self):
        other = self.root / 'Other'
        other.mkdir()
        pending = other / '.bundle-pending'
        pending.write_text('b' * 64)
        with self.assertRaises(ValueError):
            choose_destination(other, self.default, self.metadata)
        pending.write_text('a' * 64)
        self.assertEqual(choose_destination(other, self.default, self.metadata), other)
        self.ready(other)
        (other / '.bundle-ready.json').write_text(json.dumps({'payload_sha256': 'b' * 64}))
        with self.assertRaisesRegex(ValueError, 'another build'):
            choose_destination(other, self.default, self.metadata)

if __name__ == '__main__':
    unittest.main()
