"""Conservative, optional combining of repeated, header-aligned SSTV images."""
import numpy as np
from PIL import Image


class FragmentCombiner:
    """Bounded to 12 recent originals; never match a composite to new evidence."""
    def __init__(self):
        self.originals = []

    @staticmethod
    def match(first, second):
        if first.size != second.size or str(first.info['sstv_mode']) != str(second.info['sstv_mode']):
            return False
        a, b = np.asarray(first,dtype=float), np.asarray(second,dtype=float)
        qa, qb = first.info.get('sstv_confidence'), second.info.get('sstv_confidence')
        if qa is None or qb is None:
            return False
        # Shared banners, borders, callsigns and logos cannot establish identity.
        h,w = qa.shape
        crop = (slice(h//5,4*h//5,3),slice(w//8,7*w//8,3))
        good = (qa[crop]>.8)&(qb[crop]>.8)
        if good.sum() < max(200,good.size*.2):
            return False
        # Demand evidence across the interior, not a single surviving stripe.
        cells = [part for rows in np.array_split(good,3,axis=0)
                 for part in np.array_split(rows,3,axis=1)]
        if sum(np.mean(cell)>.15 for cell in cells) < 5:
            return False
        x,y = a[crop][good],b[crop][good]
        if min(np.std(x.mean(axis=1)),np.std(y.mean(axis=1))) < 20:
            return False
        error = np.mean(abs(x-y),axis=1)
        return bool(np.median(error)<15 and np.quantile(error,.85)<35 and
                    np.corrcoef(x.ravel(),y.ravel())[0,1]>.95)

    def add(self, image, seconds):
        matches = [(im,t) for im,t in self.originals if self.match(im,image)]
        self.originals.append((image.copy(),seconds))
        self.originals = self.originals[-12:]
        if not matches:
            return None
        sources = matches+[(image,seconds)]
        values = np.stack([np.asarray(im,dtype=np.float32) for im,_ in sources])
        quality = np.stack([im.info['sstv_confidence'] for im,_ in sources])
        # Exclude damaged samples wherever a stronger observation is present.
        best = quality.max(axis=0)
        weights = np.where(quality>=np.maximum(.5,best-.15),quality**4,0)
        total = weights.sum(axis=0)
        pixels = np.sum(values*weights[...,None],axis=0)/np.maximum(total[...,None],1e-10)
        # No reliable observation: retain the best actual sample, never invent it.
        fallback = np.take_along_axis(values,quality.argmax(axis=0)[None,...,None],axis=0)[0]
        pixels = np.where((total>0)[...,None],pixels,fallback)
        result = Image.fromarray(np.clip(np.rint(pixels),0,255).astype('uint8'))
        result.info.update(image.info)
        for key in ('sstv_missing_sync','sstv_missing_rows'):
            result.info.pop(key,None)
        result.info.update(sstv_confidence=best,sstv_complete=False,
                           sstv_recovery='confidence-weighted composite',
                           sstv_composite_sources=[float(t) for _,t in sources])
        return result
