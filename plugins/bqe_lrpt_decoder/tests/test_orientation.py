import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from plugins.bqe_lrpt_decoder import orientation, service
from plugins.bqe_lrpt_decoder.config import Options
from plugins import weather_gallery


ORBIT = dict(satellite='M2-4',
    line1='1 59051U 24039A   26277.63516794  .00000009  00000+0  23657-4 0  9998',
    line2='2 59051  98.7164 235.6242 0006407 205.7797 154.3063 14.22439018134829')


def job(stamp='2026-10-04T18:41:58Z'):
    return dict(options=Options().as_dict(), captured_at=stamp, orbit=copy.deepcopy(ORBIT),
                samples=900*240000, sample_rate=240000, images=[])


class OrientationTests(unittest.TestCase):
    def test_actual_morning_and_evening_pass_directions(self):
        north = orientation.direction(job())
        south = orientation.direction(job('2026-10-04T07:19:39Z'))
        self.assertEqual((north['direction'], north['rotation_degrees']), ('south_to_north', 180))
        self.assertEqual((south['direction'], south['rotation_degrees']), ('north_to_south', 0))

    def test_missing_stale_wrong_orbit_and_polar_turn_are_unknown(self):
        cases = [dict(captured_at=None), dict(captured_at='2026-10-04T18:41:58'),
                 dict(captured_at='2025-10-04T18:41:58Z'), dict(orbit={}),
                 dict(orbit='invalid'), dict(samples=0),
                 dict(captured_at='2026-10-04T18:56:58Z')]
        for changes in cases:
            with self.subTest(changes=changes):
                result = orientation.direction(dict(job(), **changes))
                self.assertEqual(result['direction'], 'unknown', result)
                self.assertEqual(result['rotation_degrees'], 0)
                self.assertTrue(result['reason'])
        wrong = job()
        wrong['options']['satellite'] = 'M2-3'
        self.assertEqual(orientation.direction(wrong)['direction'], 'unknown')

    def test_rotation_preserves_pixels_originals_projections_and_gallery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = weather_gallery.archive_root(root)/'pass'
            products = folder/'products/MSU-MR'
            products.mkdir(parents=True)
            image = Image.new('L', (3, 2))
            image.putdata([1, 2, 3, 4, 5, 6])
            for name in ('channel.png', 'rgb_map.png', 'rgb_projected.png'):
                image.save(products/name)
            original = (products/'channel.png').read_bytes()
            value = job()
            value.update(products=str(folder/'products'),
                         images=['products/MSU-MR/'+p.name for p in products.iterdir()])
            orientation.orient_images(value, folder)
            orientation.orient_images(value, folder)  # Safe to run again; no double flip.
            self.assertEqual(len(value['orientation']['replacements']), 2)
            self.assertEqual((products/'channel.png').read_bytes(), original)
            with Image.open(folder/'products/north_up/MSU-MR/channel.png') as rotated:
                self.assertEqual(list(rotated.getdata()), [6, 5, 4, 3, 2, 1])
            self.assertIn('products/MSU-MR/rgb_projected.png', value['images'])
            (folder/'job.json').write_text(json.dumps(value))
            displayed = weather_gallery.passes(root)[0]['images']
            self.assertEqual(len(displayed), 3)
            self.assertNotIn('pass/products/MSU-MR/channel.png', displayed)
            self.assertIsNotNone(weather_gallery.resolve_image(root, 'pass/products/MSU-MR/channel.png'))

    def test_unreadable_image_does_not_discard_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            value = job()
            value.update(products=directory, images=['bad.png'])
            (Path(directory)/'bad.png').write_bytes(b'bad image')
            orientation.orient_images(value, directory)
            self.assertEqual(value['images'], ['bad.png'])
            self.assertEqual(len(value['orientation']['errors']), 1)

    def test_descending_images_remain_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'channel.png'
            Image.new('RGB', (4, 3), 'red').save(path)
            before = path.read_bytes()
            value = job('2026-10-04T07:19:39Z')
            value.update(products=directory, images=['channel.png'])
            orientation.orient_images(value, directory)
            self.assertEqual(value['images'], ['channel.png'])
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((Path(directory)/'north_up').exists())

    def test_replay_keeps_archived_orbit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(service, 'executable'), \
                patch.object(service, 'launch_job'), patch.object(service, 'snapshot_orbit', return_value={}):
            path = Path(directory)/'capture.sigmf-data'
            path.write_bytes(bytes(2048))
            path.with_suffix('.sigmf-meta').write_text(json.dumps({
                'global': {'core:datatype':'cu8', 'core:sample_rate':240000, 'bqe:orbit':ORBIT},
                'captures':[{'core:sample_start':0, 'core:frequency':137100000,
                             'core:datetime':'2026-10-04T18:41:58Z'}]}))
            value = service.Session(Path(directory)/'jobs').replay(path, {})
            self.assertEqual(value['orbit'], ORBIT)
            self.assertEqual(value['captured_at'], '2026-10-04T18:41:58Z')


if __name__ == '__main__':
    unittest.main()
