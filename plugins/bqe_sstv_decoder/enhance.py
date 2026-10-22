"""Offline, lossless enhancement of matching gallery images. Never edits inputs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
from scipy.ndimage import median_filter, uniform_filter

from .storage import load_confidence, save_confidence

EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp'}
MAX_PIXELS = 2_000_000


def estimated_quality(pixels):
    """Image-only heuristic for old files; this is not measured signal quality."""
    smooth = median_filter(pixels, size=(3, 3, 1))
    residual = np.mean(abs(pixels - smooth), axis=2)
    noise = uniform_filter(residual, size=7)
    quality = np.clip(np.exp(-noise / 18), .02, .95).astype(np.float32)
    # Entire black rows are common in incomplete decodes.
    quality[np.max(pixels, axis=(1, 2)) < 3] = 0
    return quality


def load_image(path):
    with Image.open(path) as source:
        if source.width * source.height > MAX_PIXELS or min(source.size) < 32:
            raise ValueError('Image dimensions are outside the supported range')
        image = source.convert('RGB')
    metadata = {}
    sidecar = path.with_suffix('.json')
    if (sidecar.is_file() and sidecar.resolve().parent == path.parent.resolve()
            and sidecar.stat().st_size < 256_000):
        try:
            metadata = json.loads(sidecar.read_text(encoding='utf-8'))
            if not isinstance(metadata, dict):
                metadata = {}
        except (OSError, ValueError):
            pass
    quality = load_confidence(path, image)
    measured = quality is not None and metadata.get('confidence_kind', 'signal') == 'signal'
    pixels = np.asarray(image, dtype=np.float32)
    if quality is None:
        quality = estimated_quality(pixels)
    return dict(name=path.name, pixels=pixels, quality=quality,
                quality_kind='signal' if measured else 'estimated',
                mode=metadata.get('mode'), metadata=metadata)


def translated(array, dy, dx):
    """Integer registration without wrapping pixels around the canvas."""
    result = np.zeros_like(array)
    height, width = array.shape[:2]
    if abs(dy) >= height or abs(dx) >= width:
        return result
    result[max(0, dy):min(height, height+dy), max(0, dx):min(width, width+dx)] = \
        array[max(0, -dy):min(height, height-dy), max(0, -dx):min(width, width-dx)]
    return result


def alignment(first, second):
    """Match distributed interior evidence, then refine a modest translation."""
    if first['pixels'].shape != second['pixels'].shape:
        return None
    if first['mode'] and second['mode'] and first['mode'] != second['mode']:
        return None
    h, w = first['quality'].shape
    step = max(1, round(max(h, w) / 128))
    a = uniform_filter(first['pixels'], size=(3, 3, 1))[::step, ::step]
    b = uniform_filter(second['pixels'], size=(3, 3, 1))[::step, ::step]
    qa, qb = first['quality'][::step, ::step], second['quality'][::step, ::step]
    ih, iw = qa.shape
    interior = np.zeros_like(qa, dtype=bool)
    interior[ih//5:4*ih//5, iw//8:7*iw//8] = True
    required = max(80, int(interior.sum() * .15))
    threshold = .90 if first['quality_kind'] == second['quality_kind'] == 'signal' else .35

    def score(dy, dx):
        other = translated(b, dy, dx)
        good = interior & (qa > threshold) & (translated(qb, dy, dx) > threshold)
        if good.sum() < required:
            return None
        x, y = a[good], other[good]
        if min(np.std(x.mean(axis=1)), np.std(y.mean(axis=1))) < 16:
            return None
        error = np.mean(abs(x-y), axis=1)
        correlation = np.corrcoef(x.ravel(), y.ravel())[0, 1]
        # A few damaged but tonally confident pixels can spoil global Pearson
        # correlation. Also require a strong fit in the best 80% of the overlap.
        consistent = error <= np.quantile(error, .8)
        robust = np.corrcoef(x[consistent].ravel(), y[consistent].ravel())[0, 1]
        if (not np.isfinite(correlation) or not np.isfinite(robust) or
                correlation < .80 or robust < .93 or
                np.median(error) > 24 or np.quantile(error, .85) > 52):
            return None
        # A shared title or small logo must not be enough to merge photographs.
        matches = good & (np.mean(abs(a-other), axis=2) < 35)
        crop = matches[ih//5:4*ih//5, iw//8:7*iw//8]
        cells = [cell for rows in np.array_split(crop, 3) for cell in np.array_split(rows, 3, axis=1)]
        if sum(np.mean(cell) > .12 for cell in cells) < 5:
            return None
        return float(np.mean(np.minimum(error, 60)) + (1-correlation)*50)

    best = None
    for dy in range(-8, 9):
        for dx in range(-6, 7):
            value = score(dy, dx)
            if value is not None and (best is None or value < best[0]):
                best = (value, dy*step, dx*step)
    if best is None:
        return None
    # Refine to whole source pixels rather than resampling the originals.
    a, b = first['pixels'], second['pixels']
    qa, qb = first['quality'], second['quality']
    ih, iw = qa.shape
    interior = np.zeros_like(qa, dtype=bool)
    interior[ih//5:4*ih//5, iw//8:7*iw//8] = True
    required = max(200, int(interior.sum()*.15))
    refined = None
    for dy in range(best[1]-step+1, best[1]+step):
        for dx in range(best[2]-step+1, best[2]+step):
            value = score(dy, dx)
            if value is not None and (refined is None or value < refined[0]):
                refined = (value, dy, dx)
    return refined[1:] if refined else None


def combine(members):
    reference = members[0][0]
    shape = reference['pixels'].shape
    # Accumulate without allocating a full stack of potentially large images.
    best = np.zeros(shape[:2], dtype=np.float32)
    fallback = reference['pixels'].copy()
    for item, dy, dx in members:
        q = translated(item['quality'], dy, dx)
        pixels = translated(item['pixels'], dy, dx)
        better = q > best
        fallback[better] = pixels[better]
        best = np.maximum(best, q)
    total = np.zeros_like(best)
    summed = np.zeros(shape, dtype=np.float32)
    for item, dy, dx in members:
        q = translated(item['quality'], dy, dx)
        weights = np.where(q >= np.maximum(.25, best-.15), q**4, 0)
        total += weights
        summed += translated(item['pixels'], dy, dx) * weights[..., None]
    result = np.where((total > 0)[..., None], summed / np.maximum(total[..., None], 1e-12), fallback)
    return Image.fromarray(np.clip(np.rint(result), 0, 255).astype('uint8')), best


def enhance_files(directory, names, report=lambda **state: None, cancelled=lambda: False):
    directory = Path(directory).resolve()
    if not isinstance(names, list) or not 2 <= len(names) <= 24 or len(set(names)) != len(names):
        raise ValueError('Select between 2 and 24 different images')
    groups, skipped, outputs = [], [], []
    report(state='running', completed=0, total=len(names), outputs=[], message='Comparing selected images')
    for index, name in enumerate(names):
        if cancelled():
            return dict(state='cancelled', outputs=outputs, skipped=skipped, message='Cancelled; originals preserved')
        if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.lower() not in EXTENSIONS:
            raise ValueError('Invalid gallery image name')
        path = (directory/name).resolve()
        if path.parent != directory:
            raise ValueError('Image must be inside the gallery')
        try:
            item = load_image(path)
        except (OSError, ValueError) as exc:
            skipped.append(dict(filename=name, reason=str(exc)))
            report(completed=index+1, skipped=skipped)
            continue
        placed = False
        for group in groups:
            if cancelled():
                return dict(state='cancelled', outputs=outputs, skipped=skipped, message='Cancelled; originals preserved')
            offset = alignment(group[0][0], item)
            if offset is not None:
                group.append((item, *offset))
                placed = True
                break
        if not placed:
            groups.append([(item, 0, 0)])
        report(completed=index+1, message=f'Analyzed {index+1} of {len(names)} images')
    unmatched = [group[0][0]['name'] for group in groups if len(group) == 1]
    for group in groups:
        if cancelled():
            return dict(state='cancelled', outputs=outputs, skipped=skipped, message='Cancelled; completed results preserved')
        if len(group) < 2:
            continue
        image, quality = combine(group)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        path = directory / f'sstv-enhanced-{stamp}-{uuid.uuid4().hex[:8]}.png'
        temporary = path.with_suffix('.tmp')
        try:
            image.save(temporary, format='PNG')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        record = dict(filename=path.name, mode=group[0][0]['mode'], complete=False,
                      enhancement='aligned confidence-weighted composite',
                      confidence_kind='signal' if all(item['quality_kind']=='signal' for item,_,_ in group) else 'estimated',
                      sources=[dict(filename=item['name'], offset_y=dy, offset_x=dx,
                                    confidence=item['quality_kind']) for item,dy,dx in group])
        try:
            record['confidence_file'] = save_confidence(path, image, quality)
            path.with_suffix('.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        except OSError as exc:
            record['warning'] = f'Image saved; sidecar could not be saved: {exc}'
        outputs.append(record)
        report(outputs=outputs)
    return dict(state='completed', completed=len(names), total=len(names), outputs=outputs,
                skipped=skipped, unmatched=unmatched,
                message=f'Saved {len(outputs)} enhanced image(s). Originals preserved. '
                        f'{len(unmatched)} image(s) had no reliable match; {len(skipped)} could not be read.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True, type=Path)
    args = parser.parse_args()
    job = args.job
    state = dict(state='running', outputs=[])
    def report(**updates):
        state.update(updates)
        temporary = job / 'status.tmp'
        temporary.write_text(json.dumps(state), encoding='utf-8')
        temporary.replace(job / 'status.json')
    try:
        request = json.loads((job/'request.json').read_text(encoding='utf-8'))
        result = enhance_files(request['directory'], request['names'], report,
                               lambda: (job/'cancel').exists())
        report(**result)
    except Exception as exc:
        report(state='error', message=str(exc))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
