"""MicroPython tool: terminal connection"""

AVAILABLE = False

try:
    import sys as _sys
    import select as _select
    import tty as _tty
    import termios as _termios

    AVAILABLE = True

    class Terminal:
        def __init__(self, conn, log):
            self._log = log
            self._conn = conn
            self._stdin_fd = _sys.stdin.fileno()
            self._last_attr = _termios.tcgetattr(self._stdin_fd)
            self._running = None

        def __del__(self):
            if self._last_attr:
                _termios.tcsetattr(self._stdin_fd, _termios.TCSANOW, self._last_attr)

        def read(self):
            return _sys.stdin.buffer.raw.read(1)

        def write(self, buf):
            _sys.stdout.buffer.raw.write(buf)

        def _read_event_terminal(self):
            data = self.read()
            self._log.info('from terminal: %s', data)
            if 0x1d in data:  # CTRL + ]
                self._running = False
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

        def _read_event(self, event):
            if self._stdin_fd in event:
                self._read_event_terminal()
            if self._conn.fd in event:
                self._read_event_device()

        def run(self):
            _tty.setraw(self._stdin_fd)
            self._running = True
            try:
                self._flush_device()
                select_fds = [self._stdin_fd, self._conn.fd, ]
                self._log.info("select: %s", select_fds)
                while self._running:
                    ret = _select.select(select_fds, [], [], 1)
                    self._log.info("selected: %s", ret)
                    if ret[0]:
                        self._read_event(ret[0])
            except OSError as err:
                if self._log:
                    self._log.error("OSError: %s", err)
            _termios.tcsetattr(self._stdin_fd, _termios.TCSANOW, self._last_attr)
            self._last_attr = None
            self.write(b'\r\n')


except ImportError:
    pass
