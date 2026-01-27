"""Tests for mpy module"""

import unittest
from unittest.mock import Mock, patch
from mpytool.mpy import _escape_path, Mpy
import mpytool.mpy_comm as mpy_comm


class TestEscapePath(unittest.TestCase):
    def test_no_escape_needed(self):
        self.assertEqual(_escape_path("simple.txt"), "simple.txt")
        self.assertEqual(_escape_path("/path/to/file"), "/path/to/file")

    def test_escape_apostrophe(self):
        self.assertEqual(_escape_path("file's.txt"), "file\\'s.txt")
        self.assertEqual(_escape_path("it's a file"), "it\\'s a file")

    def test_escape_backslash(self):
        self.assertEqual(_escape_path("path\\file"), "path\\\\file")

    def test_escape_both(self):
        self.assertEqual(_escape_path("it's\\here"), "it\\'s\\\\here")

    def test_multiple_apostrophes(self):
        self.assertEqual(_escape_path("a'b'c"), "a\\'b\\'c")


class TestFileInfo(unittest.TestCase):
    """Tests for Mpy.fileinfo method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()

    def test_fileinfo_returns_dict(self):
        """Test that fileinfo returns dictionary from device"""
        expected = {
            "/a.txt": (100, b"hash_a"),
            "/b.txt": (200, b"hash_b"),
        }
        self.mpy._mpy_comm.exec_eval.return_value = expected
        result = self.mpy.fileinfo({"/a.txt": 100, "/b.txt": 200})
        self.assertEqual(result, expected)

    def test_fileinfo_escapes_paths(self):
        """Test that paths are escaped in the command"""
        self.mpy._mpy_comm.exec_eval.return_value = {}
        self.mpy.fileinfo({"/file's.txt": 100})
        call_args = self.mpy._mpy_comm.exec_eval.call_args
        # Check that escaped path is in the command (backslash before apostrophe)
        self.assertIn("file\\\\'s.txt", call_args[0][0])

    def test_fileinfo_timeout_scales_with_files(self):
        """Test that timeout scales with number of files"""
        self.mpy._mpy_comm.exec_eval.return_value = {}
        files = {f"/file{i}.txt": 100 for i in range(10)}
        self.mpy.fileinfo(files)
        call_args = self.mpy._mpy_comm.exec_eval.call_args
        # timeout = 5 + 10 * 0.5 = 10
        self.assertEqual(call_args[1]["timeout"], 10.0)

    def test_fileinfo_returns_none_on_error(self):
        """Test that fileinfo returns None when device raises error"""
        self.mpy._mpy_comm.exec_eval.side_effect = mpy_comm.CmdError("cmd", b"", b"error")
        result = self.mpy.fileinfo({"/a.txt": 100})
        self.assertIsNone(result)

    def test_fileinfo_loads_helper(self):
        """Test that fileinfo loads required helper"""
        self.mpy._mpy_comm.exec_eval.return_value = {}
        self.mpy.fileinfo({"/a.txt": 100})
        # Check that helper was loaded (exec called with helper code)
        exec_calls = [str(c) for c in self.mpy._mpy_comm.exec.call_args_list]
        helper_loaded = any("_mpytool_fileinfo" in c for c in exec_calls)
        self.assertTrue(helper_loaded)

    def test_fileinfo_passes_sizes(self):
        """Test that expected sizes are passed to device"""
        self.mpy._mpy_comm.exec_eval.return_value = {}
        self.mpy.fileinfo({"/a.txt": 100, "/b.txt": 200})
        call_args = self.mpy._mpy_comm.exec_eval.call_args
        # Check that sizes are in the command
        self.assertIn("100", call_args[0][0])
        self.assertIn("200", call_args[0][0])


if __name__ == "__main__":
    unittest.main()
