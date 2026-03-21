"""MicroPython tool: socket connector"""

import socket as _socket
import ssl as _ssl
import mpytool.conn as _conn


class ConnSocket(_conn.Conn):
    def __init__(
            self, address, log=None, ssl=False, ssl_verify=True,
            ssl_ca=None, ssl_check_hostname=True):
        super().__init__(log)
        self._socket = None
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.settimeout(5)
        if ':' in address:
            host, port = address.split(':')
            port = int(port)
        else:
            host = address
            port = 23
        proto = "ssl" if ssl else "tcp"
        if log:
            log.info(f"Connecting to: {host}:{port} [{proto}]")
        try:
            sock.connect((host, port))
        except _socket.timeout as err:
            sock.close()
            raise _conn.ConnError(f"Timeout connecting to {host}:{port}") from err
        except _socket.error as err:
            sock.close()
            raise _conn.ConnError(f"Cannot connect to {host}:{port}: {err}") from err
        if ssl:
            try:
                context = _ssl.create_default_context()
                if not ssl_verify:
                    context.check_hostname = False
                    context.verify_mode = _ssl.CERT_NONE
                elif not ssl_check_hostname:
                    context.check_hostname = False
                if ssl_ca:
                    context.load_verify_locations(ssl_ca)
                sock = context.wrap_socket(sock, server_hostname=host)
            except (_ssl.SSLError, OSError) as err:
                sock.close()
                raise _conn.ConnError(f"SSL error: {err}") from err
        sock.settimeout(None)
        sock.setblocking(False)
        self._socket = sock
        if log:
            log.info("connected")

    def close(self):
        if self._socket:
            self._socket.close()
            self._socket = None

    def __del__(self):
        self.close()

    @property
    def fd(self):
        return self._socket.fileno() if self._socket else None

    def _read_available(self):
        """Read available data from socket"""
        if self._socket is None:
            raise _conn.ConnError("Not connected")
        try:
            data = self._socket.recv(4096)
            if data:
                self._log.debug("RX: %r", data)
                return data
            # recv() returned b'' = peer closed connection
            raise _conn.ConnError("Connection closed")
        except (BlockingIOError, _ssl.SSLWantReadError):
            pass
        except _conn.ConnError:
            raise
        except OSError as err:
            raise _conn.ConnError(f"Connection lost: {err}") from err
        return None

    def _write_raw(self, data):
        """Write data to socket"""
        if self._socket is None:
            raise _conn.ConnError("Not connected")
        self._log.debug("TX: %r", data)
        try:
            return self._socket.send(data)
        except _ssl.SSLWantWriteError:
            return 0
        except OSError as err:
            raise _conn.ConnError(f"Connection lost: {err}") from err
