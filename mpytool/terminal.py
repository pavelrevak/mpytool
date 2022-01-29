"""MicroPython tool: terminal connection"""

AVAILABLE = False

try:
    import sys as _sys
    import select as _select
    import tty as _tty
    import termios as _termios

    AVAILABLE = True

    class Terminal:
        def __init__(self):
            self._stdin_fd = _sys.stdin.fileno()
            self._last_attr = _termios.tcgetattr(self._stdin_fd)

        def __del__(self):
            if self._last_attr:
                _termios.tcsetattr(self._stdin_fd, _termios.TCSANOW, self._last_attr)

        def read(self):
            return _sys.stdin.buffer.raw.read(1)

        def write(self, buf):
            _sys.stdout.buffer.raw.write(buf)

        def run(self, conn):
            _tty.setraw(self._stdin_fd)
            select_fds = [self._stdin_fd, conn.fd, ]
            while True:
                ret = _select.select(select_fds, [], [], 1)
                if self._stdin_fd in ret[0]:
                    data = self.read()
                    if 0x1d in data:
                        break
                    conn.write(data)
                if conn.fd in ret[0]:
                    data = conn.read()
                    self.write(data)
            _termios.tcsetattr(self._stdin_fd, _termios.TCSANOW, self._last_attr)
            self._last_attr = None
            self.write(b'\r\n')


except ImportError:
    pass
