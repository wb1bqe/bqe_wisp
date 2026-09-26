import ctypes as C
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from plugins.bqe_sdr.config import Settings
from plugins.bqe_sdr.sources import TCPSource, USBSource, FileSource, recv_exact, BLOCK


class SourceTests(unittest.TestCase):
    def test_fragmented_tcp_header_commands_and_iq(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1',0)); listener.listen()
        commands, errors = [], []
        payload = bytes(range(256))*(BLOCK*2//256)
        def server():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(3)
                    header = struct.pack('>4sII', b'RTL0', 5, 29)
                    for b in header:
                        connection.sendall(bytes([b]))
                    for _ in range(6):
                        commands.append(struct.unpack('>BI', recv_exact(connection,5)))
                    connection.sendall(bytes(240000*2//4)+payload)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=server);worker.start()
        try:
            settings = Settings.from_dict(dict(source='tcp', tcp_port=listener.getsockname()[1],
                                                sample_rate=240000,gain=20,ppm=-2))
            with TCPSource(settings, threading.Event()) as source:
                self.assertEqual(next(iter(source)),payload)
            worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors,errors)
            self.assertEqual(commands,[(2,240000),(5,0xfffffffe),(1,145860000),(8,0),(3,1),(4,200)])
        finally:
            listener.close()

    def test_tcp_disconnect_is_not_silent_eof(self):
        sock = Mock()
        sock.recv.side_effect = [b'abc',b'']
        with self.assertRaisesRegex(RuntimeError,'disconnected'):
            recv_exact(sock,8)

    def test_wrong_tcp_server_is_closed(self):
        sock = Mock()
        sock.recv.return_value = b'NOPE'+bytes(8)
        with patch('plugins.bqe_sdr.sources.socket.create_connection',return_value=sock):
            with self.assertRaisesRegex(ValueError,'RTL0'):
                TCPSource(Settings(),threading.Event()).__enter__()
        sock.close.assert_called_once()

    def test_usb_callback_lifecycle_and_configuration(self):
        lib = Mock()
        lib.rtlsdr_get_device_count.return_value = 1
        def opened(handle,index):
            handle._obj.value=123
            return 0
        lib.rtlsdr_open.side_effect=opened
        for name in ('set_sample_rate','set_center_freq','set_agc_mode','set_tuner_gain_mode',
                     'reset_buffer','cancel_async','close'):
            getattr(lib,'rtlsdr_'+name).return_value=0
        lib.rtlsdr_get_freq_correction.return_value=0
        stopped=threading.Event()
        lib.rtlsdr_cancel_async.side_effect=lambda handle: stopped.set() or 0
        raw=bytes([127,128])*BLOCK
        def read(handle,callback,ctx,n,length):
            buffer=(C.c_ubyte*len(raw)).from_buffer_copy(raw)
            callback(buffer,len(raw),None)
            stopped.wait(3)
            return 0
        lib.rtlsdr_read_async.side_effect=read
        with patch('plugins.bqe_sdr.sources.load_library',return_value=lib):
            source=USBSource(Settings.from_dict({}),threading.Event())
            with source:
                self.assertEqual(next(iter(source)),raw)
            self.assertFalse(source.thread.is_alive())
            lib.rtlsdr_close.assert_called_once()
            lib.rtlsdr_set_sample_rate.assert_called_once()

    def test_usb_setup_failure_releases_device(self):
        lib=Mock();lib.rtlsdr_get_device_count.return_value=1
        def opened(handle,index):
            handle._obj.value=123
            return 0
        lib.rtlsdr_open.side_effect=opened
        lib.rtlsdr_set_sample_rate.return_value=-22
        with patch('plugins.bqe_sdr.sources.load_library',return_value=lib):
            with self.assertRaisesRegex(RuntimeError,'set_sample_rate'):
                USBSource(Settings.from_dict({}),threading.Event()).__enter__()
        lib.rtlsdr_close.assert_called_once()

    def test_truncated_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'bad.iq';file.write_bytes(b'abc')
            settings=Settings.from_dict(dict(source='file',iq_file=str(file)))
            with FileSource(settings,threading.Event()) as source:
                with self.assertRaisesRegex(ValueError,'incomplete'):
                    next(iter(source))


if __name__ == '__main__':
    unittest.main()
