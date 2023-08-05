"""MicroPython tool: serial connector"""

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
        sock.settimeout(.1)
        if ':' in address:
            address, port = address.split(':')
            port = int(port)
        else:
            port = 23
        log.info(f"Connecting to: {address}:{port}")
        try:
            sock.connect((address, port))
        # except _serial.serialutil.SerialException as err:
        #     self._serial = None
        #     raise _conn.ConnError(
        #         f"Error opening serial port {serial_config['port']}") from err
        except _socket.timeout as err:
            log.error("Timeout while connecting to terminal. %s", err)
        except _socket.error as err:
            log.error("Can not connect to terminal. %s", err)
        else:
            sock.settimeout(None)
            sock.setblocking(0)
            self._socket = sock
            log.info("connected")

    def __del__(self):
        if self._socket:
            self._socket.close()

    @property
    def fd(self):
        return self._socket.fileno() if self._socket else None

    def flush(self):
        buffer = bytes(self._buffer)
        del self._buffer[:]
        return buffer

    def read(self):
        buff = self._socket.recv(4096)
        if buff:
            return buff
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
            self._buffer += self._socket.recv(4096)
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
