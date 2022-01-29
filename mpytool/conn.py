"""MicroPython tool: abstract connector"""


class ConnError(Exception):
    """General connection error"""


class Timeout(ConnError):
    """Timeout"""


class Conn():
    def __init__(self, log=None):
        self._log = log

    @property
    def fd(self):
        """Return file descriptor
        """
        return None

    def read(self):
        """Read available data from device
        """

    def write(self, data, chunk_size=128, delay=0.01):
        """Write to device
        """

    def read_until(self, end, timeout=1):
        """Read until
        """
        return ''

    def read_line(self, timeout=None):
        """Read signle line"""
        line = self.read_until(b'\n', timeout)
        return line.strip(b'\r')
