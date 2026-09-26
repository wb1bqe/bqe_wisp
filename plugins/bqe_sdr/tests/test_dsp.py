import unittest
import numpy as np
from plugins.bqe_sdr.config import Settings, RATES
from plugins.bqe_sdr.dsp import Demodulator, decode_iq


def signal(settings, seconds=.2, tone=1000, offset=0):
    t = np.arange(round(settings.sample_rate*seconds))/settings.sample_rate
    phase = 2*np.pi*(settings.frequency-settings.center+offset)*t
    if settings.mode == 'NFM':
        return .6*np.exp(1j*(phase-settings.deviation/tone*.4*np.cos(2*np.pi*tone*t)))
    if settings.mode == 'AM':
        return .5*(1+.5*np.sin(2*np.pi*tone*t))*np.exp(1j*phase)
    if settings.mode == 'LSB':
        tone = -tone
    if settings.mode == 'CW':
        tone = 0
    return .5*np.exp(1j*(phase+2*np.pi*tone*t))


class DSPTests(unittest.TestCase):
    def test_each_mode_recovers_expected_tone(self):
        for mode in ('NFM', 'AM', 'USB', 'LSB', 'CW'):
            with self.subTest(mode=mode):
                settings = Settings.from_dict(dict(mode=mode, bandwidth=3000 if mode in ('USB', 'LSB') else 25000))
                audio = Demodulator(settings).push(signal(settings))[2400:]
                spec = abs(np.fft.rfft(audio*np.hanning(len(audio))))
                peak = np.argmax(spec)*48000/len(audio)
                self.assertAlmostEqual(peak, 700 if mode == 'CW' else 1000, delta=8)
                self.assertGreater(np.sqrt(np.mean(audio**2)), .1)
                self.assertLess(np.max(np.abs(audio)), 1)

    def test_arbitrary_block_boundaries_preserve_samples_and_phase(self):
        for mode in ('NFM', 'AM', 'USB', 'LSB', 'CW'):
            settings = Settings.from_dict(dict(mode=mode, bandwidth=3000))
            iq = signal(settings, seconds=.08)
            whole = Demodulator(settings).push(iq)
            demod = Demodulator(settings)
            chunked = np.concatenate([demod.push(iq[i:i+1237]) for i in range(0,len(iq),1237)])
            np.testing.assert_allclose(chunked, whole, atol=2e-6)

    def test_all_sample_rates_produce_48000_audio(self):
        for rate in RATES:
            s = Settings.from_dict(dict(sample_rate=rate))
            out = Demodulator(s).push(signal(s, .02))
            self.assertEqual(len(out), 960)

    def test_opposite_sideband_rejection(self):
        s = Settings.from_dict(dict(mode='USB', bandwidth=3000))
        wanted = Demodulator(s).push(signal(s))[2400:]
        unwanted = Demodulator(s).push(signal(s, tone=-1000))[2400:]
        self.assertLess(np.linalg.norm(unwanted)/np.linalg.norm(wanted), .02)

    def test_frequency_translation_follows_doppler(self):
        s = Settings.from_dict(dict(mode='USB', bandwidth=3000))
        x = signal(s, offset=9000)
        out = Demodulator(s).push(x, s.frequency+9000)[2400:]
        peak = np.argmax(abs(np.fft.rfft(out)))*48000/len(out)
        self.assertAlmostEqual(peak, 1000, delta=8)

    def test_iq_unsigned_and_complex_formats(self):
        x = decode_iq(bytes([0,255,128,127]))
        np.testing.assert_allclose(x, [-127.5/128+1j*127.5/128, .5/128-1j*.5/128])
        expected = np.array([.3+.2j, -.1-.7j], dtype='<c8')
        np.testing.assert_array_equal(decode_iq(expected.tobytes(),'cf32_le'), expected)

    def test_validation_rejects_invalid_capture_before_open(self):
        for options in (dict(sample_rate=48000),dict(frequency=float('nan')),
                        dict(mode='WFM'),dict(bandwidth=100000),dict(audio_gain=0),
                        dict(center=1),dict(record_iq='false'),dict(source='file'),
                        dict(gain=float('inf')),dict(duration=-1)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                Settings.from_dict(options)


if __name__ == '__main__':
    unittest.main()
