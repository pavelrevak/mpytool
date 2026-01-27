"""MicroPython tool: abstract connector"""

import time as _time
import select as _select


class ConnError(Exception):
    """General connection error"""


class Timeout(ConnError):
    """Timeout"""


class Conn():
    def __init__(self, log=None):
        self._log = log
        self._buffer = bytearray(b'')

    @property
    def fd(self):
        """Return file descriptor for select()"""
        return None

    def _has_data(self, timeout=0):
        """Check if data is available to read using select()"""
        fd = self.fd
        if fd is None:
            return False
        readable, _, _ = _select.select([fd], [], [], timeout)
        return bool(readable)

    def _read_available(self):
        """Read available data from device (must be implemented by subclass)"""
        raise NotImplementedError

    def _write_raw(self, data):
        """Write data to device, return bytes written (must be implemented by subclass)"""
        raise NotImplementedError

    def _read_to_buffer(self, wait_timeout=0):
        """Read available data into buffer

        Arguments:
            wait_timeout: how long to wait for data (0 = non-blocking)
        """
        if self._has_data(wait_timeout):
            data = self._read_available()
            if data:
                self._buffer += data
                return True
        return False

    def flush(self):
        """Flush and return buffer contents"""
        buffer = bytes(self._buffer)
        del self._buffer[:]
        return buffer

    def read(self):
        """Read available data from device"""
        if self._has_data():
            return self._read_available()
        return None

    def write(self, data):
        """Write data to device"""
        if self._log:
            self._log.debug("wr: %s", bytes(data))
        while data:
            count = self._write_raw(data)
            data = data[count:]

    def read_until(self, end, timeout=1):
        """Read until end marker is found"""
        if self._log:
            self._log.debug("wait for %s", end)
        start_time = _time.time()
        while True:
            # Use select() with 1ms timeout instead of sleep - wakes immediately on data
            if self._read_to_buffer(wait_timeout=0.001):
                start_time = _time.time()  # reset timeout on data received
            if end in self._buffer:
                break
            if timeout is not None and start_time + timeout < _time.time():
                if self._buffer:
                    raise Timeout(
                        f"During timeout received: {bytes(self._buffer)}")
                raise Timeout("No data received")
        index = self._buffer.index(end)
        data = self._buffer[:index]
        del self._buffer[:index + len(end)]
        if self._log:
            self._log.debug("rd: %s", bytes(data + end))
        return data

    def read_line(self, timeout=None):
        """Read single line"""
        line = self.read_until(b'\n', timeout)
        return line.strip(b'\r')

    def hard_reset(self):
        """Hardware reset (only available on serial connections)"""
        raise NotImplementedError("Hardware reset not available on this connection")

    def reset_to_bootloader(self):
        """Reset into bootloader mode (only available on serial connections)"""
        raise NotImplementedError("Reset to bootloader not available on this connection")

    def reconnect(self, timeout=None):
        """Reconnect after device reset (only available on serial connections)"""
        raise NotImplementedError("Reconnect not available on this connection")
