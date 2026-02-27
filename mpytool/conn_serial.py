"""MicroPython tool: serial connector"""

import time as _time
import serial as _serial
import mpytool.conn as _conn
import mpytool.utils as _utils


class ConnSerial(_conn.Conn):
    RECONNECT_TIMEOUT = 10  # seconds

    def __init__(self, log=None, **serial_config):
        super().__init__(log)
        self._serial_config = serial_config
        self._port_info = _utils.get_port_info(serial_config.get('port', ''))
        try:
            self._serial = _serial.Serial(**serial_config)
        except _serial.serialutil.SerialException as err:
            self._serial = None
            raise _conn.ConnError(
                f"Error opening serial port {serial_config['port']}") from err
        # Windows: pyserial has no fd, select() works only with sockets
        if not hasattr(self._serial, 'fd'):
            self._has_data = self._has_data_polling
        # Debug info about port type
        port = serial_config.get('port', '')
        if self._port_info and self._port_info.vid:
            vid = self._port_info.vid
            pid = self._port_info.pid
            self._log.info(
                "Connected to %s [%04X:%04X %s]",
                port, vid, pid, self.port_type)
        else:
            self._log.info("Connected to %s [unknown serial port]", port)

    def close(self):
        if self._serial:
            # Prevent ESP32 reset when driver clears DTR/RTS on close
            # Only for USB-UART bridges (CP2102, CH340, etc.), not USB-CDC
            if self._is_usb_uart():
                try:
                    self._serial.rts = False
                    self._serial.dtr = False
                except OSError:
                    pass
            self._serial.close()
            self._serial = None

    def __del__(self):
        self.close()

    @property
    def fd(self):
        return self._serial.fd if self._serial else None

    def _has_data_polling(self, timeout=0):
        """Check if data is available using in_waiting (Windows)"""
        try:
            if self._serial.in_waiting > 0:
                return True
            if timeout > 0:
                deadline = _time.time() + timeout
                while _time.time() < deadline:
                    if self._serial.in_waiting > 0:
                        return True
                    _time.sleep(0.001)
            return False
        except OSError:
            return False

    def _read_available(self):
        """Read available data from serial port"""
        if self._serial is None:
            raise _conn.ConnError("Not connected")
        try:
            in_waiting = self._serial.in_waiting
            if in_waiting > 0:
                data = self._serial.read(in_waiting)
                if data:
                    self._log.debug("RX: %r", data)
                return data
            return None
        except OSError as err:
            raise _conn.ConnError(f"Connection lost: {err}") from err

    def _write_raw(self, data):
        """Write data to serial port"""
        if self._serial is None:
            raise _conn.ConnError("Not connected")
        self._log.debug("TX: %r", data)
        try:
            return self._serial.write(data)
        except OSError as err:
            raise _conn.ConnError(f"Connection lost: {err}") from err

    @property
    def vid(self):
        """USB Vendor ID or None if not available"""
        return self._port_info.vid if self._port_info else None

    @property
    def pid(self):
        """USB Product ID or None if not available"""
        return self._port_info.pid if self._port_info else None

    @property
    def port_type(self):
        """Return port type string for debug info"""
        if self._is_usb_uart():
            return "USB-UART"
        if self.vid in _utils._MICROPYTHON_VIDS:
            return "USB-CDC"
        if self.vid:
            return "USB (unknown)"
        return "unknown"

    def _is_usb_uart(self):
        """Check if this is a USB-UART bridge (CP210x, CH340, FTDI, etc.)"""
        return self.vid in _utils._USB_UART_VIDS

    def hard_reset(self):
        """Hardware reset using DTR/RTS signals (USB-UART only)"""
        if not self._is_usb_uart():
            raise _conn.ConnError(
                f"Hardware reset not supported on {self.port_type}")
        self._log.info("hard_reset: using DTR/RTS")
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
        """Reset into bootloader mode (USB-UART only)"""
        if not self._is_usb_uart():
            raise _conn.ConnError(
                f"Bootloader reset not supported on {self.port_type}")
        self._log.info("reset_to_bootloader: using DTR/RTS")
        self._serial.setDTR(False)  # GPIO0 high
        self._serial.setRTS(True)   # Assert reset
        _time.sleep(0.1)
        self._serial.setDTR(True)   # GPIO0 low (bootloader)
        self._serial.setRTS(False)  # Release reset
        _time.sleep(0.05)
        self._serial.setDTR(False)  # GPIO0 high
