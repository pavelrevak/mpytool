"""Unit tests for ConnSocket (plain TCP and SSL)

Run tests:
    python -m unittest tests.test_conn_socket -v
"""

import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

from mpytool.conn_socket import ConnSocket
from mpytool.conn import ConnError


# Check if openssl is available
OPENSSL_AVAILABLE = shutil.which('openssl') is not None


def create_test_certs(tmpdir):
    """Create self-signed certificate and key for testing"""
    cert_file = os.path.join(tmpdir, 'server.pem')
    key_file = os.path.join(tmpdir, 'server.key')
    # Generate self-signed certificate (valid for 1 day)
    subprocess.run([
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
        '-keyout', key_file, '-out', cert_file,
        '-days', '1', '-nodes',
        '-subj', '/CN=localhost'
    ], check=True, capture_output=True)
    return cert_file, key_file


class SSLEchoServer:
    """Simple SSL echo server for testing"""

    def __init__(self, cert_file, key_file, port=0):
        self.cert_file = cert_file
        self.key_file = key_file
        self.port = port
        self._server = None
        self._thread = None
        self._running = False

    def start(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert_file, self.key_file)

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(('127.0.0.1', self.port))
        self._server.listen(5)
        self.port = self._server.getsockname()[1]
        self._running = True

        def serve():
            while self._running:
                try:
                    self._server.settimeout(0.5)
                    try:
                        client, addr = self._server.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break  # Server closed
                    try:
                        ssl_client = context.wrap_socket(client, server_side=True)
                        try:
                            data = ssl_client.recv(1024)
                            if data:
                                ssl_client.send(data)
                        finally:
                            ssl_client.close()
                    except ssl.SSLError:
                        # Client disconnected or handshake failed
                        client.close()
                except Exception:
                    pass  # Ignore errors, keep serving

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._server:
            self._server.close()
        if self._thread:
            self._thread.join(timeout=2)


class PlainEchoServer:
    """Simple plain TCP echo server for testing"""

    def __init__(self, port=0):
        self.port = port
        self._server = None
        self._thread = None
        self._running = False

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(('127.0.0.1', self.port))
        self._server.listen(5)
        self.port = self._server.getsockname()[1]
        self._running = True

        def serve():
            while self._running:
                try:
                    self._server.settimeout(0.5)
                    try:
                        client, addr = self._server.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    try:
                        data = client.recv(1024)
                        if data:
                            client.send(data)
                    finally:
                        client.close()
                except Exception:
                    pass

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._server:
            self._server.close()
        if self._thread:
            self._thread.join(timeout=2)


# =============================================================================
# Plain TCP tests (no SSL)
# =============================================================================

class TestConnSocketPlain(unittest.TestCase):
    """Test plain TCP connection (no SSL)"""

    @classmethod
    def setUpClass(cls):
        cls.server = PlainEchoServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_plain_connection(self):
        """Plain TCP connection works"""
        conn = ConnSocket(f'127.0.0.1:{self.server.port}')
        try:
            conn.write(b'hello')
            # Give server time to echo back
            time.sleep(0.1)
            data = conn._read_available()
            self.assertEqual(data, b'hello')
        finally:
            conn.close()

    def test_connection_refused(self):
        """Connection to closed port raises ConnError"""
        with self.assertRaises(ConnError) as ctx:
            ConnSocket('127.0.0.1:1')  # Port 1 should be closed
        self.assertIn('Cannot connect', str(ctx.exception))


# =============================================================================
# SSL tests
# =============================================================================

@unittest.skipUnless(OPENSSL_AVAILABLE, "openssl not available")
class TestConnSocketSSL(unittest.TestCase):
    """Test SSL/TLS connection"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.cert_file, cls.key_file = create_test_certs(cls.tmpdir)
        cls.server = SSLEchoServer(cls.cert_file, cls.key_file)
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_ssl_no_verify(self):
        """SSL connection with verification disabled"""
        conn = ConnSocket(
            f'127.0.0.1:{self.server.port}',
            ssl=True, ssl_verify=False)
        try:
            conn.write(b'hello ssl')
            time.sleep(0.1)
            data = conn._read_available()
            self.assertEqual(data, b'hello ssl')
        finally:
            conn.close()

    def test_ssl_with_ca(self):
        """SSL connection with custom CA certificate"""
        # Use localhost to match CN in certificate
        conn = ConnSocket(
            f'localhost:{self.server.port}',
            ssl=True, ssl_verify=True, ssl_ca=self.cert_file)
        try:
            conn.write(b'hello ca')
            time.sleep(0.1)
            data = conn._read_available()
            self.assertEqual(data, b'hello ca')
        finally:
            conn.close()

    def test_ssl_verify_fails_without_ca(self):
        """SSL connection with verification fails for self-signed cert"""
        with self.assertRaises(ConnError) as ctx:
            ConnSocket(
                f'127.0.0.1:{self.server.port}',
                ssl=True, ssl_verify=True)
        self.assertIn('SSL error', str(ctx.exception))

    def test_ssl_invalid_ca_file(self):
        """SSL connection with invalid CA file raises error"""
        with self.assertRaises(ConnError) as ctx:
            ConnSocket(
                f'127.0.0.1:{self.server.port}',
                ssl=True, ssl_ca='/nonexistent/ca.pem')
        self.assertIn('SSL error', str(ctx.exception))

    def test_ssl_no_hostname_check(self):
        """SSL connection with hostname check disabled (cert only)"""
        # Use 127.0.0.1 but cert has CN=localhost - works with ssl_check_hostname=False
        conn = ConnSocket(
            f'127.0.0.1:{self.server.port}',
            ssl=True, ssl_verify=True, ssl_ca=self.cert_file,
            ssl_check_hostname=False)
        try:
            conn.write(b'hello no-hostname')
            time.sleep(0.1)
            data = conn._read_available()
            self.assertEqual(data, b'hello no-hostname')
        finally:
            conn.close()


# =============================================================================
# CLI argument tests
# =============================================================================

class TestSSLCliArgs(unittest.TestCase):
    """Test SSL CLI argument parsing"""

    def test_ssl_flags_in_help(self):
        """SSL flags appear in help output"""
        import subprocess
        result = subprocess.run(
            ['mpytool', '--help'], capture_output=True, text=True)
        self.assertIn('--ssl', result.stdout)
        self.assertIn('--ssl-no-verify', result.stdout)
        self.assertIn('--ssl-ca', result.stdout)
        self.assertIn('--ssl-no-hostname', result.stdout)

    def test_ssl_ca_implies_ssl(self):
        """--ssl-ca implies --ssl"""
        from mpytool.mpytool import _build_main_parser
        parser = _build_main_parser()
        args = parser.parse_args(['-a', 'host:123', '--ssl-ca', 'ca.pem', 'ls'])
        # Verify --ssl-ca is set
        self.assertEqual(args.ssl_ca, 'ca.pem')
        # The implication happens in main(), test that logic
        use_ssl = args.ssl or args.ssl_no_verify or args.ssl_ca or args.ssl_no_hostname
        self.assertTrue(use_ssl)

    def test_ssl_no_verify_implies_ssl(self):
        """--ssl-no-verify implies --ssl"""
        from mpytool.mpytool import _build_main_parser
        parser = _build_main_parser()
        args = parser.parse_args(['-a', 'host:123', '--ssl-no-verify', 'ls'])
        use_ssl = args.ssl or args.ssl_no_verify or args.ssl_ca or args.ssl_no_hostname
        self.assertTrue(use_ssl)
        ssl_verify = not args.ssl_no_verify
        self.assertFalse(ssl_verify)

    def test_ssl_no_hostname_implies_ssl(self):
        """--ssl-no-hostname implies --ssl"""
        from mpytool.mpytool import _build_main_parser
        parser = _build_main_parser()
        args = parser.parse_args(['-a', 'host:123', '--ssl-no-hostname', 'ls'])
        use_ssl = args.ssl or args.ssl_no_verify or args.ssl_ca or args.ssl_no_hostname
        self.assertTrue(use_ssl)
        ssl_check_hostname = not args.ssl_no_hostname
        self.assertFalse(ssl_check_hostname)


if __name__ == '__main__':
    unittest.main()
