from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np
import soundfile as sf
from plugins.bqe_ao123_decoder.service import Session
from plugins.bqe_ao123_decoder.integration import AO123Receiver
from plugins.bqe_ao123_decoder.tests.test_decoder import packet, transfer, escape, symbols, waveform


class IntegrationTests(unittest.TestCase):
    def test_wav_session_saves_packets_and_iq(self):
        for iq in (False, True):
            with self.subTest(iq=iq), tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                original=packet()
                data=waveform(symbols([transfer(escape(original), n) for n in range(2)]),iq=iq)
                if iq:
                    data=np.column_stack((data.real,data.imag))
                audio=root/'pass.wav'
                sf.write(audio,data,48000,subtype='FLOAT')
                session=Session(root/'decoded')
                session.start({'input_format':'iq' if iq else 'audio'},audio)
                session.thread.join(30)
                self.assertFalse(session.thread.is_alive())
                state=session.snapshot()
                self.assertEqual(state['state'],'completed',state.get('error'))
                self.assertEqual(state['packets'],2)
                rows=[json.loads(line) for line in Path(state['log']).read_text().splitlines()]
                self.assertEqual(rows[0]['frame_hex'],original.hex())
                self.assertTrue(Path(state['kiss']).stat().st_size)
                self.assertEqual(rows[0]['satellite']['norad'],61781)

    def test_pass_source_requirements_and_lifecycle(self):
        receiver=AO123Receiver(key='decode_ao123_telemetry',root=Path(__file__).resolve().parents[1],port=8783,label='AO-123')
        process=Mock()
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen',return_value=process) as launch:
            self.assertIsNone(receiver.start({},'AO-123',{}))
            with self.assertRaisesRegex(ValueError,'not FM'):
                receiver.start({'decode_ao123_telemetry':True,'downlink_mode':'FM'},'AO-123',{})
            launch.assert_not_called()
            receiver.start({'decode_ao123_telemetry':True,'downlink_mode':'BPSK','receive_sdr':True},'AO-123',{'sdr':{'port':8772}})
            command=launch.call_args.args[0]
            self.assertIn('bqe-sdr://127.0.0.1:8772',command)
            self.assertIn('--managed',command)
            self.assertEqual(command[command.index('--carrier')+1],'12000.0')
            receiver.stop()
            process.stdin.close.assert_called_once()
            process.wait.assert_called_once()
