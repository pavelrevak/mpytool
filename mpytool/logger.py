"""Simple color logger for mpytool"""

import os as _os
import sys as _sys


class SimpleColorLogger():
    # ANSI color codes
    _RESET = '\033[0m'
    _BOLD_RED = '\033[1;31m'
    _BOLD_YELLOW = '\033[1;33m'
    _BOLD_MAGENTA = '\033[1;35m'
    _BOLD_BLUE = '\033[1;34m'
    _BOLD_GREEN = '\033[1;32m'
    _BOLD_CYAN = '\033[1;36m'
    _CLEAR_LINE = '\033[K'

    # Color names for verbose()
    COLORS = {
        'red': _BOLD_RED,
        'yellow': _BOLD_YELLOW,
        'magenta': _BOLD_MAGENTA,
        'blue': _BOLD_BLUE,
        'green': _BOLD_GREEN,
        'cyan': _BOLD_CYAN,
    }

    def __init__(self, loglevel=1, verbose_level=0):
        self._loglevel = loglevel
        self._verbose_level = verbose_level
        self._is_tty = _sys.stderr.isatty()
        self._color = (
            self._is_tty
            and _os.environ.get('NO_COLOR') is None
            and _os.environ.get('TERM') != 'dumb'
            and _os.environ.get('CI') is None
            and (_sys.platform != 'win32' or _os.environ.get('TERM'))
        )

    def log(self, msg):
        print(msg, file=_sys.stderr)

    def error(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 1:
            if self._color:
                self.log(f"{self._BOLD_RED}{msg}{self._RESET}")
            else:
                self.log(f"E: {msg}")

    def warning(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 2:
            if self._color:
                self.log(f"{self._BOLD_YELLOW}{msg}{self._RESET}")
            else:
                self.log(f"W: {msg}")

    def info(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 3:
            if self._color:
                self.log(f"{self._BOLD_MAGENTA}{msg}{self._RESET}")
            else:
                self.log(f"I: {msg}")

    def debug(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 4:
            if self._color:
                self.log(f"{self._BOLD_BLUE}{msg}{self._RESET}")
            else:
                self.log(f"D: {msg}")

    def verbose(self, msg, level=1, color='green', end='\n', overwrite=False):
        """Print verbose message if verbose_level >= level"""
        if self._verbose_level < level:
            return
        # Skip progress updates (overwrite without newline) in non-TTY mode
        if overwrite and not self._is_tty and end != '\n':
            return
        color_code = self.COLORS.get(color, self._BOLD_GREEN) if self._color else ''
        reset_code = self._RESET if self._color else ''
        clear = f'\r{self._CLEAR_LINE}' if self._color and overwrite else ('\r' if self._is_tty and overwrite else '')
        print(f"{clear}{color_code}{msg}{reset_code}", end=end, file=_sys.stderr, flush=True)
