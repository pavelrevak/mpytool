"""Tests for mpy module"""

import unittest
from mpytool.mpy import _escape_path


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


if __name__ == "__main__":
    unittest.main()
