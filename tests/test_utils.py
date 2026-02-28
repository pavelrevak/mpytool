"""Tests for utility functions"""

import unittest
from unittest.mock import patch
from mpytool.utils import (
    is_remote_path,
    parse_remote_path,
    split_commands,
    detect_serial_ports,
    detect_serial_ports_info,
)


class TestRemotePath(unittest.TestCase):
    def test_is_remote_path_with_colon(self):
        self.assertTrue(is_remote_path(":/"))
        self.assertTrue(is_remote_path(":/dir/file.py"))
        self.assertTrue(is_remote_path(":"))

    def test_is_remote_path_without_colon(self):
        self.assertFalse(is_remote_path("/local/path"))
        self.assertFalse(is_remote_path("relative/path"))
        self.assertFalse(is_remote_path("file.py"))
        self.assertFalse(is_remote_path("path:with:colons"))

    def test_parse_remote_path_root(self):
        self.assertEqual(parse_remote_path(":/"), "/")

    def test_parse_remote_path_cwd(self):
        # ':' alone means CWD (empty string)
        self.assertEqual(parse_remote_path(":"), "")

    def test_parse_remote_path_absolute(self):
        self.assertEqual(parse_remote_path(":/dir/file.py"), "/dir/file.py")
        self.assertEqual(parse_remote_path(":/lib"), "/lib")

    def test_parse_remote_path_relative(self):
        self.assertEqual(parse_remote_path(":file.py"), "file.py")
        self.assertEqual(parse_remote_path(":dir/file.py"), "dir/file.py")

    def test_parse_remote_path_not_remote(self):
        with self.assertRaises(ValueError):
            parse_remote_path("/local/path")
        with self.assertRaises(ValueError):
            parse_remote_path("file.py")

    def test_parse_remote_path_with_special_chars(self):
        # mpremote issue #18658 - equals sign in filename
        self.assertEqual(parse_remote_path(":file=value.txt"), "file=value.txt")
        self.assertEqual(parse_remote_path(":/path/file=1.txt"), "/path/file=1.txt")
        # mpremote issue #18657 - apostrophe in filename
        self.assertEqual(parse_remote_path(":file's.txt"), "file's.txt")
        self.assertEqual(parse_remote_path(":/it's/file.txt"), "/it's/file.txt")
        # Spaces
        self.assertEqual(parse_remote_path(":file name.txt"), "file name.txt")
        self.assertEqual(parse_remote_path(":/my path/file.txt"), "/my path/file.txt")

    def test_parse_remote_path_with_unicode(self):
        # mpremote issues #18656, #18659, #18643 - unicode handling
        self.assertEqual(parse_remote_path(":súbor.txt"), "súbor.txt")
        self.assertEqual(parse_remote_path(":/cesta/súbor.txt"), "/cesta/súbor.txt")
        # Chinese
        self.assertEqual(parse_remote_path(":文件.txt"), "文件.txt")
        # Japanese
        self.assertEqual(parse_remote_path(":ファイル.txt"), "ファイル.txt")
        # Emoji
        self.assertEqual(parse_remote_path(":📁file.txt"), "📁file.txt")

    def test_is_remote_path_with_special_chars(self):
        # Paths with special characters should still be detected as remote
        self.assertTrue(is_remote_path(":file=value.txt"))
        self.assertTrue(is_remote_path(":file's.txt"))
        self.assertTrue(is_remote_path(":súbor.txt"))
        self.assertTrue(is_remote_path(":文件.txt"))


class TestSplitCommands(unittest.TestCase):
    def test_no_separator(self):
        self.assertEqual(
            split_commands(["cp", "file", ":/"]),
            [["cp", "file", ":/"]]
        )

    def test_single_separator(self):
        self.assertEqual(
            split_commands(["cp", "file", ":/", "--", "reset"]),
            [["cp", "file", ":/"], ["reset"]]
        )

    def test_multiple_separators(self):
        self.assertEqual(
            split_commands(["del", "old.py", "--", "cp", "new.py", ":/", "--", "reset", "--", "follow"]),
            [["del", "old.py"], ["cp", "new.py", ":/"], ["reset"], ["follow"]]
        )

    def test_empty_list(self):
        self.assertEqual(split_commands([]), [])

    def test_only_separator(self):
        self.assertEqual(split_commands(["--"]), [])

    def test_separator_at_start(self):
        self.assertEqual(
            split_commands(["--", "reset"]),
            [["reset"]]
        )

    def test_separator_at_end(self):
        self.assertEqual(
            split_commands(["reset", "--"]),
            [["reset"]]
        )

    def test_consecutive_separators(self):
        self.assertEqual(
            split_commands(["reset", "--", "--", "follow"]),
            [["reset"], ["follow"]]
        )

    def test_custom_separator(self):
        self.assertEqual(
            split_commands(["a", ";", "b"], separator=";"),
            [["a"], ["b"]]
        )


class TestDetectSerialPorts(unittest.TestCase):
    def _make_port(self, device, vid=None):
        """Helper to create mock port info"""
        return type('PortInfo', (), {'device': device, 'vid': vid})()

    @patch("mpytool.utils._comports")
    def test_filters_non_usb(self, mock_comports):
        """Non-USB devices (vid=None) should be filtered out"""
        mock_comports.return_value = [
            self._make_port('/dev/cu.usbmodem1234', vid=0x2E8A),
            self._make_port('/dev/cu.Bluetooth-Incoming-Port', vid=None),
        ]
        ports = detect_serial_ports()
        self.assertEqual(ports, ['/dev/cu.usbmodem1234'])

    @patch("mpytool.utils._comports")
    def test_prioritizes_micropython_vids(self, mock_comports):
        """Known MicroPython VIDs should come first"""
        mock_comports.return_value = [
            self._make_port('COM5', vid=0x10C4),   # CP210x (USB-UART)
            self._make_port('COM3', vid=0x2E8A),  # RP2040 (MicroPython)
            self._make_port('COM4', vid=0x1234),  # Unknown USB
        ]
        ports = detect_serial_ports()
        self.assertEqual(ports[0], 'COM3')  # RP2040 first
        self.assertEqual(ports[1], 'COM5')  # USB-UART second
        self.assertEqual(ports[2], 'COM4')  # Unknown last

    @patch("mpytool.utils._comports")
    def test_prioritizes_usb_uart_over_unknown(self, mock_comports):
        """USB-UART bridges should come before unknown USB devices"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyUSB0', vid=0x1A86),  # CH340
            self._make_port('/dev/ttyUSB1', vid=0x9999),  # Unknown
        ]
        ports = detect_serial_ports()
        self.assertEqual(ports[0], '/dev/ttyUSB0')

    @patch("sys.platform", "darwin")
    @patch("mpytool.utils._comports")
    def test_macos_filters_tty(self, mock_comports):
        """macOS should filter out tty.* ports (prefer cu.*)"""
        mock_comports.return_value = [
            self._make_port('/dev/cu.usbmodem1234', vid=0x2E8A),
            self._make_port('/dev/tty.usbmodem1234', vid=0x2E8A),
        ]
        ports = detect_serial_ports()
        self.assertEqual(ports, ['/dev/cu.usbmodem1234'])

    @patch("mpytool.utils._comports")
    def test_no_ports_found(self, mock_comports):
        mock_comports.return_value = []
        ports = detect_serial_ports()
        self.assertEqual(ports, [])

    @patch("mpytool.utils._comports")
    def test_all_known_vids(self, mock_comports):
        """Test all known MicroPython and USB-UART VIDs"""
        from mpytool.utils import (
            VID_RASPBERRY_PI, VID_ESPRESSIF, VID_MICROPYTHON,
            VID_SILICON_LABS, VID_QINHENG, VID_FTDI, VID_PROLIFIC
        )
        mock_comports.return_value = [
            self._make_port('/dev/a', vid=VID_RASPBERRY_PI),
            self._make_port('/dev/b', vid=VID_ESPRESSIF),
            self._make_port('/dev/c', vid=VID_MICROPYTHON),
            self._make_port('/dev/d', vid=VID_SILICON_LABS),
            self._make_port('/dev/e', vid=VID_QINHENG),
            self._make_port('/dev/f', vid=VID_FTDI),
            self._make_port('/dev/g', vid=VID_PROLIFIC),
        ]
        ports = detect_serial_ports()
        # All 7 ports should be included
        self.assertEqual(len(ports), 7)
        # MicroPython VIDs first (a, b, c), then USB-UART (d, e, f, g)
        self.assertEqual(ports[:3], ['/dev/a', '/dev/b', '/dev/c'])


class TestDetectSerialPortsInfo(unittest.TestCase):
    def _make_port(self, device, vid=None, pid=None,
            product=None, manufacturer=None, serial_number=None):
        return type('PortInfo', (), {
            'device': device, 'vid': vid, 'pid': pid,
            'product': product, 'manufacturer': manufacturer,
            'serial_number': serial_number,
        })()

    @patch("mpytool.utils._comports")
    def test_returns_tuples(self, mock_comports):
        """Returns (device, description) tuples with VID:PID lookup"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyACM0', vid=0x2E8A, pid=5,
                product='Pico W'),
        ]
        result = detect_serial_ports_info()
        # VID:PID lookup takes precedence over pyserial product
        self.assertEqual(result, [('/dev/ttyACM0', 'Raspberry Pi Pico W')])

    @patch("mpytool.utils._comports")
    def test_fallback_product(self, mock_comports):
        """Falls back to product when VID:PID not in table"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyUSB0', vid=0x10C4, pid=0x1234,
                product='Custom Board'),
        ]
        result = detect_serial_ports_info()
        self.assertEqual(result[0][1], 'Custom Board')

    @patch("mpytool.utils._comports")
    def test_fallback_manufacturer(self, mock_comports):
        """Falls back to manufacturer when no product"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyUSB0', vid=0x10C4, pid=0x1234,
                manufacturer='Silicon Labs'),
        ]
        result = detect_serial_ports_info()
        self.assertEqual(result[0][1], 'Silicon Labs')

    @patch("mpytool.utils._comports")
    def test_fallback_vid_name(self, mock_comports):
        """Falls back to VID name when PID unknown"""
        mock_comports.return_value = [
            # Unknown PID, falls back to VID name
            self._make_port('/dev/ttyACM0', vid=0x2E8A, pid=0x9999),
        ]
        result = detect_serial_ports_info()
        self.assertEqual(result[0][1], 'Raspberry Pi')

    @patch("mpytool.utils._comports")
    def test_known_vidpid_esp32(self, mock_comports):
        """Known VID:PID for ESP32"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyACM0', vid=0x303A, pid=0x1001),
        ]
        result = detect_serial_ports_info()
        self.assertEqual(result[0][1], 'Espressif ESP32-C3/C6')

    @patch("mpytool.utils._comports")
    def test_serial_number_with_vidpid(self, mock_comports):
        """Serial number is appended for known VID:PID"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyACM0', vid=0x2E8A, pid=5,
                serial_number='E6614C311B432839'),
        ]
        result = detect_serial_ports_info()
        self.assertEqual(
            result[0][1], 'Raspberry Pi Pico W [E6614C311B432839]')

    @patch("mpytool.utils._comports")
    def test_serial_number_with_product(self, mock_comports):
        """Serial number is appended for product fallback"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyUSB0', vid=0x10C4, pid=0x1234,
                product='Custom Board', serial_number='0001'),
        ]
        result = detect_serial_ports_info()
        self.assertEqual(result[0][1], 'Custom Board [0001]')

    @patch("mpytool.utils._comports")
    def test_fallback_vid_pid_hex(self, mock_comports):
        """Falls back to VID:PID hex when VID unknown"""
        mock_comports.return_value = [
            self._make_port('/dev/ttyUSB0', vid=0x1234, pid=0x5678),
        ]
        result = detect_serial_ports_info()
        self.assertEqual(result[0][1], 'USB 1234:5678')

    @patch("mpytool.utils._comports")
    def test_empty(self, mock_comports):
        mock_comports.return_value = []
        self.assertEqual(detect_serial_ports_info(), [])


class TestGetPortInfo(unittest.TestCase):
    @patch("mpytool.utils._comports")
    def test_found(self, mock_comports):
        from mpytool.utils import get_port_info
        mock_port = type('PortInfo', (), {
            'device': '/dev/ttyACM0', 'vid': 0x2E8A, 'pid': 0x0005
        })()
        mock_comports.return_value = [mock_port]
        info = get_port_info('/dev/ttyACM0')
        self.assertEqual(info.vid, 0x2E8A)

    @patch("mpytool.utils._comports")
    def test_not_found(self, mock_comports):
        from mpytool.utils import get_port_info
        mock_comports.return_value = []
        info = get_port_info('/dev/nonexistent')
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
