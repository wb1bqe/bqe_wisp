"""Lossless confidence sidecars, bound to the decoded RGB pixels."""
import hashlib
from pathlib import Path
import zipfile

import numpy as np


def pixel_digest(image):
    return hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()


def save_confidence(path, image, confidence):
    path = Path(path).with_suffix('.confidence.npz')
    quality = np.asarray(confidence, dtype=np.float32)
    if quality.shape != (image.height, image.width) or not np.isfinite(quality).all():
        raise ValueError('Invalid image confidence map')
    temporary = path.with_suffix('.tmp')
    try:
        with temporary.open('wb') as stream:
            np.savez_compressed(stream, confidence=np.clip(quality, 0, 1),
                                digest=np.array(pixel_digest(image)))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.name


def load_confidence(path, image):
    path = Path(path).with_suffix('.confidence.npz')
    if not path.is_file() or path.resolve().parent != path.parent.resolve():
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > image.width * image.height * 4 + 4096:
                return None
        with np.load(path, allow_pickle=False) as data:
            quality = data['confidence']
            if (str(data['digest']) != pixel_digest(image) or
                    quality.shape != (image.height, image.width) or
                    not np.isfinite(quality).all()):
                return None
            return np.clip(quality.astype(np.float32), 0, 1)
    except (OSError, ValueError, TypeError, EOFError, KeyError, zipfile.BadZipFile):
        return None
