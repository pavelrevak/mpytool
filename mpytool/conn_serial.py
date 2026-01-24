"""MicroPython tool: serial connector"""

import serial as _serial
import mpytool.conn as _conn


class ConnSerial(_conn.Conn):
    def __init__(self, log=None, **serial_config):
        super().__init__(log)
        try:
            self._serial = _serial.Serial(**serial_config)
        except _serial.serialutil.SerialException as err:
            self._serial = None
            raise _conn.ConnError(
                f"Error opening serial port {serial_config['port']}") from err

    def __del__(self):
        if self._serial:
            self._serial.close()

    @property
    def fd(self):
        return self._serial.fd if self._serial else None

    def _read_available(self):
        """Read available data from serial port"""
        in_waiting = self._serial.in_waiting
        if in_waiting > 0:
            return self._serial.read(in_waiting)
        return None

    def _write_raw(self, data):
        """Write data to serial port"""
        return self._serial.write(data)
