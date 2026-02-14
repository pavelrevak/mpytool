"""MicroPython tool: terminal connection

Base class with common terminal functionality.
Platform-specific implementations in terminal_unix.py and terminal_win.py.
"""

import sys as _sys

AVAILABLE = False


class TerminalBase:
    """Common terminal functionality for interactive REPL"""

    def __init__(self, conn, log):
        self._log = log
        self._conn = conn
        self._running = None

    def _setup(self):
        """Set terminal to raw mode (platform-specific)"""
        raise NotImplementedError

    def _restore(self):
        """Restore original terminal mode (platform-specific)"""
        raise NotImplementedError

    def read(self):
        """Read input from keyboard (platform-specific)"""
        raise NotImplementedError

    def _loop(self):
        """Main event loop (platform-specific)"""
        raise NotImplementedError

    def write(self, buf):
        _sys.stdout.buffer.raw.write(buf)

    def _read_event_terminal(self):
        data = self.read()
        self._log.info('from terminal: %s', data)
        if 0x1d in data:  # CTRL + ]
            self._running = False
            return
        if data:
            self._conn.write(data)

    def _read_event_device(self):
        data = self._conn.read()
        self._log.info('from device: %s', data)
        if data:
            self.write(data)

    def _flush_device(self):
        data = self._conn.flush()
        if data:
            self.write(data)

    def run(self):
        self._setup()
        self._running = True
        try:
            self._flush_device()
            self._loop()
        except OSError as err:
            self._log.error(err)
        finally:
            self._restore()
            self.write(b'\r\n')


try:
    from mpytool.terminal_unix import Terminal
    AVAILABLE = True
except ImportError:
    try:
        from mpytool.terminal_win import Terminal
        AVAILABLE = True
    except ImportError:
        pass
