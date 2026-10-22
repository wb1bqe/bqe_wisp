"""Orient chronological Meteor swaths without changing SatDump's source products."""
from datetime import datetime, timezone
import math
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
CATALOGS = {'M2-3': '57166', 'M2-4': '59051'}


def snapshot_orbit(options):
    """Freeze local elements at capture time; never fetch a newer orbit on replay."""
    try:
        filename = options.tle_file
        if not filename:
            import yaml
            settings = yaml.safe_load((PROJECT/'bqe_config/general_settings.yaml').read_text(encoding='utf-8')) or {}
            filename = next((entry['bqe_track_continuously'].get('tle_file', 'keps.txt')
                             for entry in settings.get('program_settings', [])
                             if 'bqe_track_continuously' in entry), 'keps.txt')
        path = Path(filename)
        if not path.is_absolute():
            path = PROJECT/path
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        catalog = CATALOGS[options.satellite]
        for first, second in zip(lines, lines[1:]):
            if first.startswith('1 '+catalog) and second.startswith('2 '+catalog):
                return dict(line1=first, line2=second, satellite=options.satellite)
        return dict(error='Satellite not found in local TLE file')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(error='Cannot read local orbital elements: '+str(exc))


def direction(job):
    """SGP4 geocentric latitude trend suffices for ascending/descending detection."""
    result = dict(direction='unknown', rotation_degrees=0, reason='')
    try:
        from sgp4.api import Satrec, jday
        orbit = job.get('orbit') or {}
        if not isinstance(orbit, dict):
            raise ValueError('Invalid saved orbital elements')
        if orbit.get('satellite') != job['options']['satellite']:
            raise ValueError('No saved orbital elements for this satellite')
        stamp = datetime.fromisoformat(str(job.get('captured_at')).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Capture time needs a UTC offset')
        stamp = stamp.astimezone(timezone.utc)
        duration = float(job['samples']) / float(job['sample_rate'])
        if not math.isfinite(duration) or not 0 < duration <= 3600:
            raise ValueError('Capture duration is unavailable or exceeds one pass')
        satellite = Satrec.twoline2rv(orbit['line1'], orbit['line2'])
        if str(satellite.satnum) != CATALOGS[job['options']['satellite']]:
            raise ValueError('Orbital elements identify a different satellite')
        jd, fraction = jday(stamp.year, stamp.month, stamp.day, stamp.hour,
                            stamp.minute, stamp.second + stamp.microsecond/1e6)
        age = jd + fraction + duration/172800 - satellite.jdsatepoch - satellite.jdsatepochF
        if abs(age) > 14:
            raise ValueError('Orbital elements are more than 14 days from capture time')
        latitudes = []
        for index in range(9):
            error, position, _ = satellite.sgp4(jd, fraction + duration*index/(8*86400))
            if error or not all(math.isfinite(x) for x in position):
                raise ValueError('Orbit propagation failed')
            latitudes.append(math.degrees(math.atan2(position[2], math.hypot(*position[:2]))))
        steps = [b-a for a, b in zip(latitudes, latitudes[1:])]
        if all(step > 1e-7 for step in steps):
            result.update(direction='south_to_north', rotation_degrees=180)
        elif all(step < -1e-7 for step in steps):
            result.update(direction='north_to_south', rotation_degrees=0)
        else:
            raise ValueError('Capture crosses an orbital latitude turning point; one rotation is insufficient')
        result.update(reason='Chronological scan lines; direction from saved TLE and IQ capture time',
                      latitudes=latitudes, tle_age_days=round(age, 3))
    except (ImportError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        result['reason'] = str(exc)
    return result


def orient_images(job, folder):
    """Create replaceable PNG views, retaining original pixels and geolocation metadata."""
    previous = (job.get('orientation') or {}).get('replacements', {})
    originals = {value: key for key, value in previous.items()}
    job['images'] = [originals.get(Path(name).as_posix(), name) for name in job['images']]
    info = direction(job)
    info.update(replacements={}, errors=[])
    job['orientation'] = info
    if info['rotation_degrees'] != 180:
        return
    try:
        from PIL import Image
    except ImportError as exc:
        info['errors'].append(str(exc))
        return
    folder = Path(folder).resolve()
    products = Path(job['products']).resolve()
    images = []
    for name in job['images']:
        source = (folder/name).resolve()
        try:
            relative = source.relative_to(products)
            # SatDump map overlays/corrected swaths retain scan order, but actual
            # cartographic projections have their own orientation already.
            if ('north_up' in relative.parts or '_projected' in source.stem.lower()
                    or any('projection' in part.lower() for part in relative.parts[:-1])):
                images.append(name)
                continue
            target = products/'north_up'/relative.with_suffix('.png')
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.tmp')
            with Image.open(source) as picture:
                rotated = picture.transpose(Image.Transpose.ROTATE_180)
                rotated.save(temporary, format='PNG')
            temporary.replace(target)
            replacement = target.relative_to(folder).as_posix()
            info['replacements'][Path(name).as_posix()] = replacement
            images.append(replacement)
        except (OSError, ValueError) as exc:
            info['errors'].append(f'{name}: {exc}')
            images.append(name)
    job['images'] = images
