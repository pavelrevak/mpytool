"""MicroPython tool: serial connector"""

import time as _time
import serial as _serial
import mpytool.conn as _conn


class ConnSerial(_conn.Conn):
    def __init__(self, log=None, **serial_config):
        super().__init__(log)
        self._buffer = bytearray(b'')
        try:
            self._serial = _serial.Serial(**serial_config)
        except _serial.serialutil.SerialException as err:
            self._serial = None
            raise _conn.ConnError(
                f"Error opening serial port {serial_config['port']}") from err

    def __del__(self):
        if self._serial:
            self._serial.close()

    def _read_to_buffer(self):
        in_waiting = self._serial.in_waiting
        if in_waiting > 0:
            self._buffer += self._serial.read(in_waiting)
            return True
        return False

    @property
    def fd(self):
        return self._serial.fd

    def flush(self):
        buffer = bytes(self._buffer)
        del self._buffer[:]
        return buffer

    def read(self):
        in_waiting = self._serial.in_waiting
        if in_waiting > 0:
            return self._serial.read(in_waiting)
        return None

    def write(self, data, chunk_size=128, delay=0.01):
        if self._log:
            self._log.debug("wr: %s", bytes(data))
        while data:
            chunk = data[:chunk_size]
            count = self._serial.write(chunk)
            data = data[count:]
            if data:
                _time.sleep(delay)

    def read_until(self, end, timeout=1):
        if self._log:
            self._log.debug("wait for %s", end)
        start_time = _time.time()
        while True:
            if self._read_to_buffer():
                start_time = _time.time()
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
