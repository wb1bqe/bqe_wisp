"""Local decoder server with reliable occupied-port detection on Windows."""
from http.server import ThreadingHTTPServer
import os
import socket
from pathlib import Path


def decoder_page(path):
    """Include the shared connection lifecycle before the page's own scripts."""
    script = Path(__file__).with_name('decoder_lifecycle.js').read_bytes()
    return path.read_bytes().replace(b'<script>', b'<script>' + script + b'</script><script>', 1)


class DecoderServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        # HTTPServer's SO_REUSEADDR can allow two listeners on the same Windows
        # port. Exclusive binding makes fallback-port handling deterministic.
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
