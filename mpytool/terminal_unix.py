"""MicroPython tool: Unix terminal (tty/termios/select)"""

import sys as _sys
import select as _select
import tty as _tty
import termios as _termios

from mpytool.terminal import TerminalBase


class Terminal(TerminalBase):

    def __init__(self, conn, log):
        super().__init__(conn, log)
        self._stdin_fd = _sys.stdin.fileno()
        self._orig_attr = _termios.tcgetattr(self._stdin_fd)

    def __del__(self):
        if self._orig_attr:
            _termios.tcsetattr(
                self._stdin_fd, _termios.TCSANOW, self._orig_attr)

    def _setup(self):
        _tty.setraw(self._stdin_fd)

    def _restore(self):
        if self._orig_attr:
            _termios.tcsetattr(
                self._stdin_fd, _termios.TCSANOW, self._orig_attr)
            self._orig_attr = None

    def read(self):
        return _sys.stdin.buffer.raw.read(1)

    def _loop(self):
        select_fds = [self._stdin_fd, self._conn.fd]
        while self._running:
            ret = _select.select(select_fds, [], [], 1)
            if ret[0]:
                if self._stdin_fd in ret[0]:
                    self._read_event_terminal()
                if self._conn.fd in ret[0]:
                    self._read_event_device()
