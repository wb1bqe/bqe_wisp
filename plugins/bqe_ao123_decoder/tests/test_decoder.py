# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import numpy as np
import reedsolo
from plugins.bqe_ao123_decoder.protocol import Transport, SymbolDecoder
from plugins.bqe_ao123_decoder.dsp import AudioDecoder


def packet(payload=b'\x01\xc0\xdb\x12AO123', apid=42, sequence=123):
    return (0x800 | apid).to_bytes(2, 'big') + (0xc000 | sequence).to_bytes(2, 'big') + (len(payload)-1).to_bytes(2, 'big') + payload


def escape(data):
    return b'\xc0' + data.replace(b'\xdb', b'\xdb\xdd').replace(b'\xc0', b'\xdb\xdc') + b'\xc0'


def transfer(data, count=0, vc=0):
    return ((123 << 4) | (vc << 1)).to_bytes(2, 'big') + bytes([count, count, 0]) + data.ljust(218, b'\xc0')


def symbols(frames, corrupt=False):
    # Independent forward encoder, per CCSDS 131.0-B: conventional RS(255,223),
    # randomization, ASM, differential precoding, K=7 [0x4f, ~0x6d].
    rs = reedsolo.RSCodec(32, nsize=255, fcr=112, prim=0x187, generator=173)
    bits = list(np.random.default_rng(19).integers(0, 2, 512))
    for frame in frames:
        code = bytearray(rs.encode(frame))
        if corrupt:
            for index in range(40 if corrupt is True else corrupt):
                code[index*5] ^= 0x5a
        register = 255
        whitening = []
        for i in range(255*8):
            whitening.append(register & 1)
            feedback = ((register >> 7) ^ (register >> 5) ^ (register >> 3) ^ register) & 1
            register = (register >> 1) | (feedback << 7)
        bits.extend(np.unpackbits(np.frombuffer(bytes.fromhex('1acffc1d'), dtype='uint8')))
        bits.extend(np.unpackbits(np.frombuffer(code, dtype='uint8')) ^ np.array(whitening))
    bits.extend([0]*1024)
    state = previous = 0
    out = []
    for bit in bits:
        previous ^= int(bit)
        state = ((state << 1) | previous) & 127
        a = sum((state >> k)&1 for k in (0, 1, 2, 3, 6)) & 1
        b = 1 ^ (sum((state >> k)&1 for k in (0, 2, 3, 5, 6)) & 1)
        out.extend([2*a-1, 2*b-1])
    return np.array(out, dtype=float)


def waveform(values, rate=48000, carrier=12000, offset=90, iq=False):
    # Fractional starting phase, sample-clock error, unknown RF phase, noise.
    t = np.arange(int((len(values)-1)*rate/(9600*1.0001)))/rate
    indexes = np.floor(t*9600*1.0001+.31).astype(int)
    base = .45*values[indexes]*np.exp(1j*(2*np.pi*(offset+(0 if iq else carrier))*t+.73))
    noise = np.random.default_rng(4).normal(0,.012,len(t))
    return base + noise if iq else base.real + noise


class DecoderTests(unittest.TestCase):
    def test_symbols_fec_and_polarity(self):
        original = packet()
        frames = [transfer(escape(original), n) for n in range(3)]
        for polarity in (1,-1):
            found=[]
            decoder=SymbolDecoder(found.append)
            stream=polarity*symbols(frames)
            for i,value in enumerate(stream):
                decoder.feed(value,i/9600)
            self.assertEqual([p['frame_hex'] for p in found], [original.hex()]*3)
            self.assertEqual(found[0]['telemetry'][0]['fields']['apid'],42)

    def test_audio_and_iq_chunks(self):
        original=packet()
        values=symbols([transfer(escape(original), n) for n in range(3)])
        for rate,iq in [(48000,False),(44100,False),(48000,True)]:
            found=[]
            decoder=AudioDecoder(rate, found.append, {'input_format':'iq' if iq else 'audio'})
            data=waveform(values,rate,iq=iq)
            for start in range(0,len(data),997):
                decoder.push(data[start:start+997])
            decoder.finish()
            self.assertEqual([p['frame_hex'] for p in found], [original.hex()]*3, decoder.diagnostics())

    def test_transport_gaps_channels_and_bounds(self):
        original=packet(b'A'*260+b'\xc0\xdb')
        stream=escape(original)
        found=[]
        decoder=Transport(found.append)
        decoder.push(transfer(stream[:218],0),0)
        decoder.push(transfer(b'\xc0',10,vc=2),.1)
        decoder.push(transfer(stream[218:],1),.2)
        self.assertEqual(found[0]['frame_hex'],original.hex())
        found.clear()
        decoder.push(transfer(stream[:218],2),.3)
        decoder.push(transfer(stream[218:],4),.4)
        self.assertEqual(found,[])
        self.assertEqual(decoder.gaps,1)
        decoder.push(transfer(escape(packet()),5),.5)
        self.assertEqual(len(found),1)
        decoder.push(transfer(escape(b'\x00'*9),6),.6)
        self.assertEqual(len(found),1)

    def test_reed_solomon_repairs_damaged_bytes(self):
        original=packet()
        found=[]
        decoder=SymbolDecoder(found.append)
        for i,value in enumerate(symbols([transfer(escape(original))],corrupt=8)):
            decoder.feed(value,i/9600)
        self.assertEqual([p['frame_hex'] for p in found],[original.hex()])
        self.assertEqual(found[0]['rs_corrected_bytes'],8)

    def test_corruption_and_noise_rejected(self):
        found=[]
        decoder=SymbolDecoder(found.append)
        values=symbols([transfer(escape(packet()))],corrupt=True)
        values=np.r_[values, np.random.default_rng(1).choice([-1,1],8000)]
        for i,value in enumerate(values):
            decoder.feed(value,i/9600)
        self.assertEqual(found,[])
        self.assertGreater(decoder.diagnostics()['bad_fec'] + decoder.diagnostics()['ignored_frames'],0)

    def test_sdr_iq_to_telemetry_with_doppler(self):
        from plugins.bqe_sdr.config import Settings
        from plugins.bqe_sdr.dsp import Demodulator
        original = packet()
        values = symbols([transfer(escape(original), n) for n in range(2)])
        settings = Settings.from_dict(dict(mode='BPSK', bandwidth=16000,
            frequency=435400000, sample_rate=240000))
        t = np.arange(int((len(values)-1)*settings.sample_rate/9600))/settings.sample_rate
        base = values[np.floor(t*9600).astype(int)]
        iq = .4*base*np.exp(2j*np.pi*(settings.frequency-settings.center+500)*t)
        demod = Demodulator(settings)
        found=[]
        decoder=AudioDecoder(48000,found.append)
        for start in range(0,len(iq),7777):
            decoder.push(demod.push(iq[start:start+7777],settings.frequency+500))
        decoder.finish()
        self.assertEqual([p['frame_hex'] for p in found],[original.hex()]*2)

    def test_invalid_inputs(self):
        for rate,opts in [(8000,{}),(48000,{'carrier':1000}),(48000,{'input_format':'fm'}),(48000,{'carrier':float('nan')})]:
            with self.assertRaises(ValueError):
                AudioDecoder(rate,lambda x:None,opts)
        with self.assertRaises(ValueError):
            AudioDecoder(48000,lambda x:None,{'input_format':'iq'}).select_samples(np.zeros((5,1)),0)


if __name__ == '__main__':
    unittest.main()
