"""Local decoder server with reliable occupied-port detection on Windows."""
from http.server import ThreadingHTTPServer
import os
import socket


class DecoderServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        # HTTPServer's SO_REUSEADDR can allow two listeners on the same Windows
        # port. Exclusive binding makes fallback-port handling deterministic.
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
