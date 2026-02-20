"""MicroPython tool: abstract connector"""

import time as _time
import select as _select

# VFS escape byte - signals start of VFS command from device
ESCAPE = 0x18


class ConnError(Exception):
    """General connection error"""


class Timeout(ConnError):
    """Timeout"""


class Conn():
    def __init__(self, log=None):
        self._log = log
        self._buffer = bytearray()
        self._escape_handlers = {}  # {escape_byte: handler}
        self._active_handler = None  # Currently processing handler

    @property
    def fd(self):
        """Return file descriptor for select()"""
        return None

    def register_escape_handler(self, escape, handler):
        """Register handler for escape byte.

        handler.process(data) returns:
            None  - need more data
            b''   - done
            bytes - done with leftovers
        """
        self._escape_handlers[escape] = handler

    def unregister_escape_handler(self, escape):
        """Unregister handler for escape byte."""
        self._escape_handlers.pop(escape, None)
        if self._active_handler is self._escape_handlers.get(escape):
            self._active_handler = None

    def _has_data(self, timeout=0):
        """Check if data is available to read using select()"""
        if self._active_handler and hasattr(self._active_handler, 'pending'):
            if self._active_handler.pending:
                return True
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

    def _process_data(self, data):
        """Process incoming data, intercept escape sequences.

        Detects registered escape bytes and routes data to handlers.
        Returns output bytes (may be empty if all data was for handler).

        Handler protocol:
            handler.process(data) returns:
            - None: handler needs more data, keep routing to it
            - b'': handler done, no leftover data
            - bytes: handler done, these are leftover bytes
        """
        if not data:
            return data

        output = b''

        while data:
            if self._active_handler:
                # Route data to active handler
                result = self._active_handler.process(data)
                if result is None:
                    # Handler needs more data
                    return output or None
                # Handler done
                self._active_handler = None
                data = result  # May be b'' or leftover bytes
            else:
                # Find registered escape byte in data
                earliest_pos = len(data)
                earliest_handler = None
                for esc, handler in self._escape_handlers.items():
                    try:
                        pos = data.index(esc)
                        if pos < earliest_pos:
                            earliest_pos = pos
                            earliest_handler = handler
                    except ValueError:
                        pass

                if earliest_handler:
                    output += data[:earliest_pos]
                    self._active_handler = earliest_handler
                    data = data[earliest_pos:]  # Include escape, handler verifies
                else:
                    output += data
                    break

        # Check for soft reboot in output (VFS-specific)
        if output and self._escape_handlers.get(ESCAPE):
            handler = self._escape_handlers[ESCAPE]
            if hasattr(handler, 'check_reboot'):
                handler.check_reboot(output)

        return output or b''

    def _read_to_buffer(self, wait_timeout=0):
        """Read available data into buffer

        Arguments:
            wait_timeout: how long to wait for data (0 = non-blocking)
        """
        if self._has_data(wait_timeout):
            data = self._read_available()
            data = self._process_data(data)
            if data:
                self._buffer += data
                return True
        return False

    def flush(self):
        """Flush and return buffer contents"""
        buffer = bytes(self._buffer)
        del self._buffer[:]
        return buffer

    @property
    def busy(self):
        """True if connection is busy with internal protocol exchange"""
        return self._active_handler is not None

    def read(self, timeout=0):
        """Read available data from device (non-blocking by default).

        Returns device output bytes, or None if no data available.
        When escape handler is active, services requests transparently.

        Arguments:
            timeout: how long to wait for data (0 = non-blocking)
        """
        if self._has_data(timeout):
            data = self._read_available()
            return self._process_data(data)
        return None

    def read_bytes(self, count, timeout=1):
        """Read exactly count bytes from device"""
        start_time = _time.time()
        while len(self._buffer) < count:
            if self._read_to_buffer(wait_timeout=0.001):
                start_time = _time.time()
            if timeout is not None and start_time + timeout < _time.time():
                if self._buffer:
                    raise Timeout(
                        f"During timeout received: {bytes(self._buffer)}")
                raise Timeout("No data received")
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    def write(self, data):
        """Write data to device"""
        while data:
            count = self._write_raw(data)
            data = data[count:]

    def read_until(self, end, timeout=1):
        """Read until end marker is found"""
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
        return data

    def read_line(self, timeout=None):
        """Read single line"""
        line = self.read_until(b'\n', timeout)
        return line.strip(b'\r')

    def close(self):
        """Close connection"""

    def hard_reset(self):
        """Hardware reset (only available on serial connections)"""
        raise NotImplementedError("Hardware reset not available on this connection")

    def reset_to_bootloader(self):
        """Reset into bootloader mode (only available on serial connections)"""
        raise NotImplementedError("Reset to bootloader not available on this connection")

    def reconnect(self, timeout=None):
        """Reconnect after device reset (only available on serial connections)"""
        raise NotImplementedError("Reconnect not available on this connection")
