"""VIS detection and a sample-clock fallback for faded SSTV transmissions.

Timing values follow the SSTV mode tables used by the MIT-licensed sstv crate
(see THIRD_PARTY.md). Missing sync never consumes later rows: advance by the
mode's line period, then reacquire only close to the predicted sync position.
"""
from dataclasses import dataclass
from math import gcd

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter1d
from scipy.signal import hilbert, resample_poly, stft
import sstv


@dataclass(frozen=True)
class Layout:
    name: str
    vis: int
    steps: tuple
    pair: int = 1
    prefix: float = 0

    @property
    def mode(self):
        return getattr(sstv.Mode, self.name)

    @property
    def period(self):
        return sum(duration for _, duration in self.steps)

    @property
    def duration(self):
        return self.prefix + self.period * self.mode.image_height / self.pair


def layouts():
    values = [
        Layout('ROBOT_36', 8, (('S',.009),('',.003),('Y',.088),('',.006),('C',.044))),
        Layout('ROBOT_72', 12, (('S',.009),('',.003),('Y',.138),('',.006),('V',.069),('',.006),('U',.069))),
        Layout('WRASSE_SC2_180',55,(('S',.0055225),('',.0005),('R',.235),('G',.235),('B',.235))),
    ]
    for name, code, scan in [('MARTIN_1',44,.146432),('MARTIN_2',40,.073216)]:
        values.append(Layout(name,code,(('S',.004862),('',.000572),('G',scan),('',.000572),('B',scan),('',.000572),('R',scan),('',.000572))))
    for name, code, scan in [('SCOTTIE_1',60,.138240),('SCOTTIE_2',56,.088064),('SCOTTIE_DX',76,.345600)]:
        values.append(Layout(name,code,(('',.0015),('G',scan),('',.0015),('B',scan),('S',.009),('',.0015),('R',scan)),prefix=.009))
    for name, code, sync, porch, scan in [('PASOKON_P3',113,.005208,.001042,.133333),('PASOKON_P5',114,.007813,.001563,.2),('PASOKON_P7',115,.010417,.002083,.266666)]:
        values.append(Layout(name,code,(('S',sync),('',porch),('R',scan),('',porch),('G',scan),('',porch),('B',scan),('',porch))))
    for suffix, code, scan in [(50,93,.091520),(90,99,.170240),(120,95,.121600),(160,98,.195584),(180,96,.183040),(240,97,.244480),(290,94,.228800)]:
        values.append(Layout('PD_'+str(suffix),code,(('S',.020),('',.002080),('Y',scan),('V',scan),('U',scan),('Z',scan)),pair=2))
    return {layout.name: layout for layout in values}


LAYOUTS = layouts()
VIS = {layout.vis: layout for layout in LAYOUTS.values()}


def find_header(audio, rate):
    """Return (layout, first-picture-sample time) after leader/framing/parity checks.

    Spectral integration tolerates noise that defeats individual zero crossings.
    Never infer a mode from picture audio alone or repair an invalid VIS parity.
    """
    if len(audio) < .65 * rate:
        return None
    factor = gcd(rate, 12000)
    samples = resample_poly(audio, 12000//factor, rate//factor)
    f, times, z = stft(samples, 12000, nperseg=240, noverlap=180, boundary=None)
    power = abs(z)**2
    energies = np.array([power[abs(f-tone)<=40].sum(axis=0) for tone in (1100,1200,1300,1900)])
    total = np.maximum(power[(f>=700)&(f<=2700)].sum(axis=0), 1e-20)
    label = energies.argmax(axis=0)
    quality = energies.max(axis=0)/total
    leader = (label == 3) & (quality > .32)
    runs = np.flatnonzero(np.diff(np.r_[False,leader,False])).reshape(-1,2)
    for a, b in runs:
        if b-a < 28 or b >= len(times):
            continue
        # Locate the leader/start transition to sub-ms accuracy using the
        # crossing of the two spectral energies, not the last whole FFT bin.
        indices = np.arange(max(a,b-3), min(len(times)-1,b+4))
        delta = energies[3]-energies[1]
        transitions = indices[(delta[indices]>=0)&(delta[indices+1]<0)]
        if not len(transitions):
            continue
        k = transitions[-1]
        start = times[k] + .005 * delta[k]/(delta[k]-delta[k+1])
        centers = start + .015 + .03*np.arange(10)
        if centers[-1]+.01 > times[-1]:
            continue
        at = np.searchsorted(times,centers)
        bits = label[at]
        if bits[0] != 1 or bits[-1] != 1 or np.any(quality[at] < .25):
            continue
        if np.any(~np.isin(bits[1:9], [0,2])):
            continue
        ones = bits[1:9] == 0
        if np.count_nonzero(ones) % 2:
            continue
        code = sum(int(bit)<<i for i,bit in enumerate(ones[:7]))
        if code in VIS:
            return VIS[code], float(start+.3)
    return None


def _tone_chunk(samples, rate):
    """Spectral tone confidence and a noise-tolerant frequency estimate.

    Short overlapping windows integrate weak tones; interpolation of the peak
    avoids FFT-bin colour banding. Confidence measures concentration above the
    broadband noise floor, independently of absolute recording volume.
    """
    f, times, z = stft(samples, rate, nperseg=96, noverlap=84, nfft=512)
    power = abs(z)**2
    band = np.flatnonzero((f >= 1000) & (f <= 2500))
    peak = band[np.argmax(power[band], axis=0)]
    col = np.arange(len(times))
    logs = np.log(np.maximum(power, 1e-20))
    left, mid, right = logs[peak-1,col], logs[peak,col], logs[peak+1,col]
    delta = .5*(left-right)/np.minimum(left-2*mid+right, -1e-12)
    hz = (peak+np.clip(delta,-.5,.5))*rate/512
    floor = np.median(power[band], axis=0)
    confidence = np.clip(1-5*floor/np.maximum(power[peak,col],1e-20),0,1)
    confidence[np.max(power[band],axis=0)<1e-12] = 0
    positions = np.arange(len(samples))/rate
    return np.interp(positions,times,hz), np.interp(positions,times,confidence)


def tone_track(samples, rate):
    # Bound FFT allocations even for the longest mode (Pasokon P7).
    hz = np.empty(len(samples),dtype=np.float64)
    quality = np.empty(len(samples),dtype=np.float32)
    for start in range(0,len(samples),rate*8):
        end = min(len(samples),start+rate*8)
        left,right = max(0,start-96),min(len(samples),end+96)
        f,q = _tone_chunk(samples[left:right],rate)
        hz[start:end],quality[start:end] = f[start-left:end-left],q[start-left:end-left]
    return hz,quality


def recover(audio, rate, layout, picture_start):
    """Decode on the original time axis, keeping rows after missing sync pulses."""
    # 12 kHz bounds FFT/memory cost for high-rate input without dropping any
    # SSTV tones. The original audio and recordings are never modified.
    factor = gcd(rate,12000)
    samples = resample_poly(audio,12000//factor,rate//factor).astype(np.float32)
    rate = 12000
    analytic = hilbert(samples)
    frequency = np.angle(analytic[1:]*np.conj(analytic[:-1])) * (rate/(2*np.pi))
    # np.interp consumes float64 arrays; convert once, rather than copying the
    # complete frequency track for every scan in a long Pasokon/PD image.
    frequency = np.r_[frequency,frequency[-1]].astype(np.float64)
    spectral, confidence = tone_track(samples, rate)
    confidence = confidence.astype(np.float64)  # Avoid a full copy for every pixel scan.
    # Phase differences retain fine detail in strong signal. During fades they
    # follow noise crossings; use integrated spectral peaks in those samples.
    weight = np.clip((confidence-.65)/.25,0,1)
    frequency = frequency*weight + spectral*(1-weight)
    # Only smooth sync detection; pixel sampling retains the scan bandwidth.
    smooth = uniform_filter1d(frequency,size=9)
    amplitude = abs(analytic)
    sync = (abs(smooth-1200)<85) & (amplitude > 2/32768) & (confidence > .75)
    runs = np.flatnonzero(np.diff(np.r_[False,sync,False])).reshape(-1,2)
    offset = 0
    for channel, duration in layout.steps:
        if channel == 'S':
            sync_offset, sync_length = offset, duration
        offset += duration
    candidates = runs[(runs[:,1]-runs[:,0]) >= sync_length*rate*.45,1]/rate
    width, height = layout.mode.image_width, layout.mode.image_height
    output = np.zeros((height,width,3),dtype=np.uint8)
    channels = []
    missing = []
    starts = []
    tracks = {1: frequency}
    sample_indices = np.arange(len(frequency),dtype=np.float64)
    origin = picture_start + layout.prefix
    # Fit reliable pulses to the header-anchored line clock. Never accumulate
    # a succession of noise-induced local timing corrections through a fade.
    rows = np.arange(height//layout.pair)
    expected = origin + rows*layout.period + sync_offset + sync_length
    matches = [(row, candidates[np.argmin(abs(candidates-t))]-t)
               for row,t in enumerate(expected) if len(candidates) and
               np.min(abs(candidates-t)) < .012]
    slope, intercept = 0., 0.
    if len(matches) >= 8:
        x, residual = np.array(matches).T
        keep = np.ones(len(x),dtype=bool)
        for _ in range(4):
            if keep.sum() < 8:
                break
            slope, intercept = np.polyfit(x[keep],residual[keep],1)
            errors = residual-(slope*x+intercept)
            keep = abs(errors) < max(.0015,3*np.median(abs(errors[keep])))
        if abs(slope) > layout.period*.002 or abs(intercept) > .012:
            slope, intercept = 0., 0.
    quality_rows = []
    for row in range(height//layout.pair):
        start = origin + row*(layout.period+slope) + intercept
        expected = start + sync_offset + sync_length
        near = candidates[abs(candidates-expected) < min(.003, sync_length*.5)]
        available = expected < len(samples)/rate
        if len(near):
            actual = near[np.argmin(abs(near-expected))]
            start += actual-expected
        elif available:
            missing.append(row)
        starts.append(start)
        values = {}
        qualities = {}
        offset = 0
        for channel, duration in layout.steps:
            if channel not in ('','S'):
                positions = (start+offset+(np.arange(width)+.5)*duration/width)*rate
                # Averaging a fraction of each pixel rejects isolated phase
                # spikes without blurring adjacent colour transitions.
                span = max(1,int(duration*rate/width*.5))
                if span not in tracks:
                    tracks[span] = uniform_filter1d(frequency,size=span)
                hz = np.interp(positions,sample_indices,tracks[span])
                values[channel] = np.clip((hz-1500)*255/800,0,255)
                qualities[channel] = np.interp(positions,sample_indices,confidence,left=0,right=0)
            offset += duration
        channels.append(values)
        quality_rows.append(qualities)

    def rgb(y,v,u):
        red = np.clip(y+1.4*(v-127.5),0,255)
        blue = np.clip(y+1.78*(u-127.5),0,255)
        green = np.clip((100*y-30*red-11*blue)/59,0,255)
        return np.column_stack((red,green,blue))

    quality_map = np.zeros((height,width),dtype=np.float32)
    blank = []
    for row, values in enumerate(channels):
        left, right = int(starts[row]*rate), int((starts[row]+layout.period)*rate)
        # Missing/squelched samples are black, never synthesized image detail.
        if left < 0 or right > len(samples)+.003*rate or (right>left and np.max(abs(samples[max(0,left):min(len(samples),right)]),initial=0)<2/32768):
            blank.append(row)
            continue
        if layout.name == 'ROBOT_36':
            even = row//2*2
            color = rgb(values['Y'],channels[even]['C'],channels[min(even+1,height-1)]['C'])
        elif layout.pair == 2:
            color = np.stack((rgb(values['Y'],values['V'],values['U']),rgb(values['Z'],values['V'],values['U'])))
        elif 'Y' in values:
            color = rgb(values['Y'],values['V'],values['U'])
        else:
            color = np.column_stack((values['R'],values['G'],values['B']))
        output[row*layout.pair:(row+1)*layout.pair] = color.astype(np.uint8)
        q = quality_rows[row]
        if layout.name == 'ROBOT_36':
            quality = np.minimum(q['Y'],np.minimum(quality_rows[even]['C'],quality_rows[min(even+1,height-1)]['C']))
        elif layout.pair == 2:
            quality = np.stack([np.minimum(q[c],np.minimum(q['V'],q['U'])) for c in ('Y','Z')])
        else:
            quality = np.minimum.reduce(list(q.values()))
        quality_map[row*layout.pair:(row+1)*layout.pair] = quality
    image = Image.fromarray(output)
    image.info['sstv_confidence'] = quality_map
    image.info.update(sstv_mode=layout.mode, sstv_complete=not missing and not blank,
                      sstv_recovery='sample-clock', sstv_missing_sync=len(missing),
                      sstv_missing_rows=len(blank)*layout.pair,
                      sstv_expected_seconds=layout.duration)
    return image
