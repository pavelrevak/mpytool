"""MicroPython tool: socket connector"""

import time as _time
import socket as _socket
import select as _select
import mpytool.conn as _conn


class ConnSocket(_conn.Conn):
    def __init__(self, address, log=None):
        super().__init__(log)
        self._buffer = bytearray(b'')
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

    def __del__(self):
        if self._socket:
            self._socket.close()

    @property
    def fd(self):
        return self._socket.fileno() if self._socket else None

    def _has_data(self, timeout=0):
        """Check if socket has data available to read"""
        if not self._socket:
            return False
        readable, _, _ = _select.select([self._socket], [], [], timeout)
        return bool(readable)

    def _read_to_buffer(self):
        """Read available data from socket to buffer"""
        if self._has_data():
            try:
                data = self._socket.recv(4096)
                if data:
                    self._buffer += data
                    return True
            except BlockingIOError:
                pass
        return False

    def flush(self):
        buffer = bytes(self._buffer)
        del self._buffer[:]
        return buffer

    def read(self):
        if self._has_data():
            try:
                data = self._socket.recv(4096)
                if data:
                    return data
            except BlockingIOError:
                pass
        return None

    def write(self, data, chunk_size=128, delay=0.01):
        if self._log:
            self._log.debug("wr: %s", bytes(data))
        while data:
            chunk = data[:chunk_size]
            count = self._socket.send(chunk)
            data = data[count:]
            if data:
                _time.sleep(delay)

    def read_until(self, end, timeout=1):
        if self._log:
            self._log.debug("wait for %s", end)
        start_time = _time.time()
        while True:
            if self._read_to_buffer():
                start_time = _time.time()  # reset timeout on data received
            if end in self._buffer:
                break
            if timeout is not None and start_time + timeout < _time.time():
                if self._buffer:
                    raise _conn.Timeout(
                        f"During timeout received: {bytes(self._buffer)}")
                raise _conn.Timeout("No data received")
            _time.sleep(.01)
        index = self._buffer.index(end)
        data = self._buffer[:index]
        del self._buffer[:index + len(end)]
        if self._log:
            self._log.debug("rd: %s", bytes(data + end))
        return data
