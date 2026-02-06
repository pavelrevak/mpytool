"""MicroPython tool: Windows terminal (msvcrt/ctypes)"""

import sys as _sys
import time as _time
import ctypes as _ctypes
import msvcrt as _msvcrt

from mpytool.terminal import TerminalBase

# kernel32 console mode constants
_STD_INPUT_HANDLE = -10
_STD_OUTPUT_HANDLE = -11
_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

_kernel32 = _ctypes.windll.kernel32

# Windows scan code to ANSI escape sequence mapping
# msvcrt.getch() returns 0x00 or 0xE0 prefix + scan code for special keys
_SCAN_TO_ANSI = {
    72: b'\x1b[A',   # Up
    80: b'\x1b[B',   # Down
    77: b'\x1b[C',   # Right
    75: b'\x1b[D',   # Left
    71: b'\x1b[H',   # Home
    79: b'\x1b[F',   # End
    83: b'\x1b[3~',  # Delete
    82: b'\x1b[2~',  # Insert
    73: b'\x1b[5~',  # Page Up
    81: b'\x1b[6~',  # Page Down
}


class Terminal(TerminalBase):

    def __init__(self, conn, log):
        super().__init__(conn, log)
        self._stdin_h = _kernel32.GetStdHandle(_STD_INPUT_HANDLE)
        self._stdout_h = _kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
        self._orig_in_mode = _ctypes.c_uint32()
        self._orig_out_mode = _ctypes.c_uint32()
        _kernel32.GetConsoleMode(
            self._stdin_h, _ctypes.byref(self._orig_in_mode))
        _kernel32.GetConsoleMode(
            self._stdout_h, _ctypes.byref(self._orig_out_mode))

    def __del__(self):
        self._restore()

    def _setup(self):
        # Input: disable line input, echo, processed input
        # enable virtual terminal input (Win10+)
        _kernel32.SetConsoleMode(
            self._stdin_h, _ENABLE_VIRTUAL_TERMINAL_INPUT)
        # Output: enable ANSI escape processing
        out_mode = self._orig_out_mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
        _kernel32.SetConsoleMode(self._stdout_h, out_mode)

    def _restore(self):
        if self._orig_in_mode.value:
            _kernel32.SetConsoleMode(self._stdin_h, self._orig_in_mode)
            self._orig_in_mode = _ctypes.c_uint32()
        if self._orig_out_mode.value:
            _kernel32.SetConsoleMode(self._stdout_h, self._orig_out_mode)
            self._orig_out_mode = _ctypes.c_uint32()

    def write(self, buf):
        _sys.stdout.buffer.raw.write(buf)
        _sys.stdout.buffer.raw.flush()

    def read(self):
        byte = _msvcrt.getch()
        if byte in (b'\x00', b'\xe0'):
            scan = _msvcrt.getch()
            return _SCAN_TO_ANSI.get(scan[0], b'')
        return byte

    def _loop(self):
        while self._running:
            activity = False
            if _msvcrt.kbhit():
                self._read_event_terminal()
                activity = True
            if self._conn._has_data(0):
                self._read_event_device()
                activity = True
            if not activity:
                _time.sleep(0.002)
