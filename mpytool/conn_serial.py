"""MicroPython tool: serial connector"""

import time as _time
import serial as _serial
import mpytool.conn as _conn


class ConnSerial(_conn.Conn):
    RECONNECT_TIMEOUT = 5  # seconds

    def __init__(self, log=None, **serial_config):
        super().__init__(log)
        self._serial_config = serial_config
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

    def _is_usb_cdc(self):
        """Check if this is a USB-CDC port (native USB on ESP32-S/C)"""
        port = self._serial.port or ''
        return 'ACM' in port or 'usbmodem' in port

    def hard_reset(self):
        """Hardware reset using DTR/RTS signals"""
        self._serial.setDTR(False)  # GPIO0 high (normal boot)
        self._serial.setRTS(True)   # Assert reset
        _time.sleep(0.1)
        self._serial.setRTS(False)  # Release reset

    def reconnect(self, timeout=None):
        """Close and reopen serial port (for USB-CDC reconnect after reset)"""
        if timeout is None:
            timeout = self.RECONNECT_TIMEOUT
        port = self._serial_config.get('port', 'unknown')
        if self._serial:
            try:
                self._serial.close()
            except (OSError, _serial.serialutil.SerialException):
                pass  # Port may already be gone
            self._serial = None
        start = _time.time()
        while _time.time() - start < timeout:
            try:
                self._serial = _serial.Serial(**self._serial_config)
                del self._buffer[:]  # Clear any stale data
                _time.sleep(0.5)  # Wait for device to stabilize
                # Verify connection is stable
                _ = self._serial.in_waiting
                return True
            except (_serial.serialutil.SerialException, OSError):
                if self._serial:
                    try:
                        self._serial.close()
                    except (OSError, _serial.serialutil.SerialException):
                        pass
                    self._serial = None
                _time.sleep(0.1)
        raise _conn.ConnError(f"Could not reconnect to {port} within {timeout}s")

    def reset_to_bootloader(self):
        """Reset into bootloader mode (auto-detects USB-CDC vs USB-UART)"""
        if self._is_usb_cdc():
            self._reset_to_bootloader_usb_jtag()
        else:
            self._reset_to_bootloader_classic()

    def _reset_to_bootloader_usb_jtag(self):
        """Bootloader reset for USB-JTAG-Serial (esptool sequence)"""
        self._serial.setRTS(False)
        self._serial.setDTR(False)
        _time.sleep(0.1)
        self._serial.setDTR(True)
        self._serial.setRTS(True)
        _time.sleep(0.1)
        self._serial.setDTR(False)
        self._serial.setRTS(True)
        _time.sleep(0.1)
        self._serial.setDTR(False)
        self._serial.setRTS(False)

    def _reset_to_bootloader_classic(self):
        """Bootloader reset for USB-UART (classic DTR/RTS circuit)"""
        self._serial.setDTR(False)  # GPIO0 high
        self._serial.setRTS(True)   # Assert reset
        _time.sleep(0.1)
        self._serial.setDTR(True)   # GPIO0 low (bootloader)
        self._serial.setRTS(False)  # Release reset
        _time.sleep(0.05)
        self._serial.setDTR(False)  # GPIO0 high
