"""Tests for error classes"""

import unittest
from mpytool.mpy_comm import MpyError, CmdError
from mpytool.mpy import PathNotFound, FileNotFound, DirNotFound


class TestMpyError(unittest.TestCase):
    def test_base_error(self):
        err = MpyError("test error")
        self.assertEqual(str(err), "test error")

    def test_is_exception(self):
        self.assertTrue(issubclass(MpyError, Exception))


class TestCmdError(unittest.TestCase):
    def test_cmd_error_with_error_only(self):
        err = CmdError("print(x)", b"", b"NameError: name 'x' is not defined")
        msg = str(err)
        self.assertIn("print(x)", msg)
        self.assertIn("NameError", msg)

    def test_cmd_error_with_result_and_error(self):
        err = CmdError("cmd", b"partial output", b"error occurred")
        msg = str(err)
        self.assertIn("cmd", msg)
        self.assertIn("partial output", msg)
        self.assertIn("error occurred", msg)

    def test_cmd_error_properties(self):
        err = CmdError("cmd", b"result", b"error")
        self.assertEqual(err.cmd, "cmd")
        self.assertEqual(err.result, b"result")
        self.assertEqual(err.error, "error")

    def test_is_mpy_error(self):
        self.assertTrue(issubclass(CmdError, MpyError))


class TestPathErrors(unittest.TestCase):
    def test_path_not_found(self):
        err = PathNotFound("/some/path")
        self.assertIn("/some/path", str(err))
        self.assertIn("Path", str(err))

    def test_file_not_found(self):
        err = FileNotFound("/some/file.txt")
        self.assertIn("/some/file.txt", str(err))
        self.assertIn("File", str(err))

    def test_dir_not_found(self):
        err = DirNotFound("/some/dir")
        self.assertIn("/some/dir", str(err))
        self.assertIn("Dir", str(err))

    def test_inheritance(self):
        self.assertTrue(issubclass(FileNotFound, PathNotFound))
        self.assertTrue(issubclass(DirNotFound, PathNotFound))
        self.assertTrue(issubclass(PathNotFound, MpyError))


if __name__ == "__main__":
    unittest.main()
