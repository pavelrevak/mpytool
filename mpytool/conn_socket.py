"""MicroPython tool: socket connector"""

import socket as _socket
import mpytool.conn as _conn


class ConnSocket(_conn.Conn):
    def __init__(self, address, log=None):
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
        if log:
            log.info(f"Connecting to: {host}:{port}")
        try:
            sock.connect((host, port))
        except _socket.timeout as err:
            raise _conn.ConnError(f"Timeout connecting to {host}:{port}") from err
        except _socket.error as err:
            raise _conn.ConnError(f"Cannot connect to {host}:{port}: {err}") from err
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
                return data
        except BlockingIOError:
            pass
        except OSError as err:
            raise _conn.ConnError(f"Connection lost: {err}") from err
        return None

    def _write_raw(self, data):
        """Write data to socket"""
        if self._socket is None:
            raise _conn.ConnError("Not connected")
        try:
            return self._socket.send(data)
        except OSError as err:
            raise _conn.ConnError(f"Connection lost: {err}") from err
