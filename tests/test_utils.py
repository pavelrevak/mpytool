"""Tests for utility functions"""

import unittest
from unittest.mock import patch
from mpytool.utils import (
    is_remote_path,
    parse_remote_path,
    split_commands,
    detect_serial_ports,
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
    @patch("sys.platform", "darwin")
    @patch("glob.glob")
    def test_macos_patterns(self, mock_glob):
        mock_glob.side_effect = [
            ["/dev/tty.usbmodem1234"],
            ["/dev/tty.usbserial-5678"],
            [],
        ]
        ports = detect_serial_ports()
        self.assertIn("/dev/tty.usbmodem1234", ports)
        self.assertIn("/dev/tty.usbserial-5678", ports)

    @patch("sys.platform", "linux")
    @patch("glob.glob")
    def test_linux_patterns(self, mock_glob):
        mock_glob.side_effect = [
            ["/dev/ttyACM0", "/dev/ttyACM1"],
            ["/dev/ttyUSB0"],
        ]
        ports = detect_serial_ports()
        self.assertIn("/dev/ttyACM0", ports)
        self.assertIn("/dev/ttyACM1", ports)
        self.assertIn("/dev/ttyUSB0", ports)

    @patch("sys.platform", "win32")
    @patch("glob.glob")
    def test_windows_not_supported(self, mock_glob):
        ports = detect_serial_ports()
        self.assertEqual(ports, [])
        mock_glob.assert_not_called()

    @patch("sys.platform", "linux")
    @patch("glob.glob")
    def test_no_ports_found(self, mock_glob):
        mock_glob.return_value = []
        ports = detect_serial_ports()
        self.assertEqual(ports, [])

    @patch("sys.platform", "linux")
    @patch("glob.glob")
    def test_removes_duplicates(self, mock_glob):
        mock_glob.side_effect = [
            ["/dev/ttyACM0"],
            ["/dev/ttyACM0"],  # duplicate
        ]
        ports = detect_serial_ports()
        self.assertEqual(ports.count("/dev/ttyACM0"), 1)


if __name__ == "__main__":
    unittest.main()
