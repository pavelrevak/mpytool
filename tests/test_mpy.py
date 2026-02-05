"""Tests for mpy module"""

import base64
import unittest
from unittest.mock import Mock, patch
from mpytool.mpy import _escape_path, Mpy
import mpytool.mpy_comm as mpy_comm


class TestEscapePath(unittest.TestCase):
    """Tests for _escape_path function

    Based on mpremote issues:
    - #18657: apostrophe in filename
    - #18658: equals sign in filename
    """
    def test_no_escape_needed(self):
        self.assertEqual(_escape_path("simple.txt"), "simple.txt")
        self.assertEqual(_escape_path("/path/to/file"), "/path/to/file")

    def test_escape_apostrophe(self):
        # mpremote issue #18657
        self.assertEqual(_escape_path("file's.txt"), "file\\'s.txt")
        self.assertEqual(_escape_path("it's a file"), "it\\'s a file")

    def test_escape_backslash(self):
        self.assertEqual(_escape_path("path\\file"), "path\\\\file")

    def test_escape_both(self):
        self.assertEqual(_escape_path("it's\\here"), "it\\'s\\\\here")

    def test_multiple_apostrophes(self):
        self.assertEqual(_escape_path("a'b'c"), "a\\'b\\'c")

    def test_equals_sign_no_escape_needed(self):
        # mpremote issue #18658 - equals sign should work without escape
        self.assertEqual(_escape_path("file=value.txt"), "file=value.txt")
        self.assertEqual(_escape_path("a=b=c.txt"), "a=b=c.txt")

    def test_spaces_no_escape_needed(self):
        # Spaces don't need escaping in Python string literals
        self.assertEqual(_escape_path("file with spaces.txt"), "file with spaces.txt")
        self.assertEqual(_escape_path("/path/to/my file.txt"), "/path/to/my file.txt")

    def test_unicode_no_escape_needed(self):
        # Unicode characters don't need escaping
        self.assertEqual(_escape_path("súbor.txt"), "súbor.txt")
        self.assertEqual(_escape_path("文件.txt"), "文件.txt")
        self.assertEqual(_escape_path("ファイル.txt"), "ファイル.txt")

    def test_special_chars_combination(self):
        # Combination of special characters
        self.assertEqual(
            _escape_path("it's a file=test.txt"),
            "it\\'s a file=test.txt"
        )

    def test_double_quotes_no_escape_needed(self):
        # Double quotes don't need escaping in single-quoted strings
        self.assertEqual(_escape_path('file"name.txt'), 'file"name.txt')


class TestFileInfo(unittest.TestCase):
    """Tests for Mpy.fileinfo method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()

    def test_fileinfo_returns_dict(self):
        """Test that fileinfo returns dictionary from device"""
        # Device returns base64 encoded hashes
        device_response = {
            "/a.txt": (100, base64.b64encode(b"hash_a")),
            "/b.txt": (200, base64.b64encode(b"hash_b")),
        }
        expected = {
            "/a.txt": (100, b"hash_a"),
            "/b.txt": (200, b"hash_b"),
        }
        self.mpy._mpy_comm.exec_eval.return_value = device_response
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
        helper_loaded = any("_mt_finfo" in c for c in exec_calls)
        self.assertTrue(helper_loaded)

    def test_fileinfo_passes_sizes(self):
        """Test that expected sizes are passed to device"""
        self.mpy._mpy_comm.exec_eval.return_value = {}
        self.mpy.fileinfo({"/a.txt": 100, "/b.txt": 200})
        call_args = self.mpy._mpy_comm.exec_eval.call_args
        # Check that sizes are in the command
        self.assertIn("100", call_args[0][0])
        self.assertIn("200", call_args[0][0])


class TestDetectChunkSize(unittest.TestCase):
    """Tests for Mpy._detect_chunk_size method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()
        # Reset class-level cache before each test
        Mpy._CHUNK_AUTO_DETECTED = None

    def tearDown(self):
        # Reset class-level cache after each test
        Mpy._CHUNK_AUTO_DETECTED = None

    def test_large_ram_uses_32k_chunks(self):
        """Test that devices with >256KB RAM use 32KB chunks"""
        self.mpy._mpy_comm.exec_eval.return_value = 300 * 1024  # 300KB
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 32768)

    def test_medium_large_ram_uses_16k_chunks(self):
        """Test that devices with >128KB RAM use 16KB chunks"""
        self.mpy._mpy_comm.exec_eval.return_value = 150 * 1024  # 150KB
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 16384)

    def test_medium_ram_uses_8k_chunks(self):
        """Test that devices with >64KB RAM use 8KB chunks"""
        self.mpy._mpy_comm.exec_eval.return_value = 80 * 1024  # 80KB
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 8192)

    def test_small_ram_uses_4k_chunks(self):
        """Test that devices with >48KB RAM use 4KB chunks"""
        self.mpy._mpy_comm.exec_eval.return_value = 55 * 1024  # 55KB
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 4096)

    def test_smaller_ram_uses_2k_chunks(self):
        """Test that devices with >32KB RAM use 2KB chunks"""
        self.mpy._mpy_comm.exec_eval.return_value = 40 * 1024  # 40KB
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 2048)

    def test_small_ram_uses_1k_chunks(self):
        """Test that devices with >24KB RAM use 1KB chunks"""
        self.mpy._mpy_comm.exec_eval.return_value = 28 * 1024  # 28KB
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 1024)

    def test_tiny_ram_uses_512_chunks(self):
        """Test that devices with <=24KB RAM use 512B chunks"""
        self.mpy._mpy_comm.exec_eval.return_value = 20 * 1024  # 20KB
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 512)

    def test_error_defaults_to_512(self):
        """Test that errors default to 512B chunks"""
        from mpytool.mpy_comm import CmdError
        self.mpy._mpy_comm.exec_eval.side_effect = CmdError("cmd", b"", b"error")
        chunk = self.mpy._detect_chunk_size()
        self.assertEqual(chunk, 512)

    def test_caches_result(self):
        """Test that chunk size is cached after first detection"""
        self.mpy._mpy_comm.exec_eval.return_value = 300 * 1024  # 300KB
        chunk1 = self.mpy._detect_chunk_size()
        # Change return value - should still use cached
        self.mpy._mpy_comm.exec_eval.return_value = 20 * 1024  # 20KB
        chunk2 = self.mpy._detect_chunk_size()
        self.assertEqual(chunk1, chunk2)
        self.assertEqual(chunk2, 32768)

    def test_user_specified_skips_detection(self):
        """Test that user-specified chunk size skips auto-detection"""
        mpy = Mpy(self.mock_conn, chunk_size=4096)
        mpy._mpy_comm = Mock()
        chunk = mpy._detect_chunk_size()
        self.assertEqual(chunk, 4096)
        # exec_eval should not be called (no RAM detection)
        mpy._mpy_comm.exec_eval.assert_not_called()


class TestEncodeChunk(unittest.TestCase):
    """Tests for Mpy._encode_chunk method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)

    def test_printable_ascii_uses_raw(self):
        """Test that printable ASCII uses raw repr (shorter)"""
        chunk = b"Hello World"
        cmd, size, enc_type = self.mpy._encode_chunk(chunk)
        self.assertEqual(size, 11)
        self.assertEqual(enc_type, 'raw')
        # Raw repr is shorter for printable ASCII
        self.assertEqual(cmd, repr(chunk))

    def test_binary_data_uses_base64(self):
        """Test that binary data with many escapes uses base64"""
        # All zeros - each byte would be \x00 (4 chars) in raw
        chunk = b"\x00" * 100
        cmd, size, enc_type = self.mpy._encode_chunk(chunk)
        self.assertEqual(size, 100)
        self.assertEqual(enc_type, 'base64')
        # Base64 should be chosen (shorter than 400 chars of \x00\x00...)
        self.assertIn("ub.a2b_base64", cmd)

    def test_returns_correct_original_size(self):
        """Test that original chunk size is returned"""
        chunk = b"test data"
        cmd, size, enc_type = self.mpy._encode_chunk(chunk)
        self.assertEqual(size, len(chunk))

    def test_compression_option(self):
        """Test that compression is tried when enabled"""
        # Highly compressible data
        chunk = b"A" * 500
        cmd, size, enc_type = self.mpy._encode_chunk(chunk, compress=True)
        self.assertEqual(size, 500)
        self.assertEqual(enc_type, 'compressed')
        # Should use deflate (much smaller than raw or base64)
        self.assertIn("df.DeflateIO", cmd)

    def test_compression_not_used_for_incompressible(self):
        """Test that compression is not used when data doesn't compress well"""
        # Random-ish binary data that doesn't compress
        import os
        chunk = os.urandom(100)
        cmd, size, enc_type = self.mpy._encode_chunk(chunk, compress=True)
        # Should not use deflate (compression overhead makes it larger)
        self.assertNotIn("df.DeflateIO", cmd)
        self.assertIn(enc_type, ('raw', 'base64'))


class TestPut(unittest.TestCase):
    """Tests for Mpy.put method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()
        self.mpy._mpy_comm.exec_eval.return_value = 512  # Bytes written
        # Set chunk size to avoid detection call
        Mpy._CHUNK_AUTO_DETECTED = 512

    def tearDown(self):
        Mpy._CHUNK_AUTO_DETECTED = None

    def test_returns_encodings_and_wire_bytes(self):
        """Test that put returns encodings set and wire bytes"""
        data = b"Hello World"
        self.mpy._mpy_comm.exec_eval.return_value = len(data)
        encodings, wire_bytes = self.mpy.put(data, "/test.txt")
        self.assertIsInstance(encodings, set)
        self.assertIsInstance(wire_bytes, int)
        self.assertGreater(wire_bytes, 0)

    def test_raw_encoding_for_text(self):
        """Test that text data uses raw encoding"""
        data = b"Hello World"
        self.mpy._mpy_comm.exec_eval.return_value = len(data)
        encodings, _ = self.mpy.put(data, "/test.txt")
        self.assertIn('raw', encodings)

    def test_base64_encoding_for_binary(self):
        """Test that binary data uses base64 or compressed encoding"""
        data = b"\x00" * 100
        self.mpy._mpy_comm.exec_eval.return_value = len(data)
        encodings, _ = self.mpy.put(data, "/test.bin")
        # Binary data uses base64 or compressed (if deflate available)
        self.assertTrue('base64' in encodings or 'compressed' in encodings)

    def test_compressed_encoding_when_enabled(self):
        """Test that compressible data uses compressed encoding"""
        data = b"A" * 1000  # Highly compressible
        self.mpy._mpy_comm.exec_eval.return_value = len(data)
        encodings, _ = self.mpy.put(data, "/test.txt", compress=True)
        self.assertIn('compressed', encodings)

    def test_wire_bytes_less_than_data_with_compression(self):
        """Test that wire bytes are less than data size with compression"""
        data = b"A" * 1000  # Highly compressible
        self.mpy._mpy_comm.exec_eval.return_value = len(data)
        _, wire_bytes = self.mpy.put(data, "/test.txt", compress=True)
        # Wire bytes should be significantly less due to compression
        # (accounting for command overhead)
        self.assertLess(wire_bytes, len(data))

    def test_wire_bytes_includes_command_overhead(self):
        """Test that wire bytes include f.write() command overhead"""
        data = b"x"  # Single byte
        self.mpy._mpy_comm.exec_eval.return_value = 1
        _, wire_bytes = self.mpy.put(data, "/test.txt")
        # Should include 9 bytes overhead for "f.write(" + ")"
        self.assertGreater(wire_bytes, len(repr(data)))

    def test_progress_callback_called(self):
        """Test that progress callback is called during transfer"""
        data = b"Hello World"
        self.mpy._mpy_comm.exec_eval.return_value = len(data)
        callback = Mock()
        self.mpy.put(data, "/test.txt", progress_callback=callback)
        callback.assert_called()


class TestFlashReadWithLabel(unittest.TestCase):
    """Tests for Mpy.flash_read with label (ESP32 partition)"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()
        self.mpy._platform = 'esp32'  # Set platform for partition operations

    def test_flash_read_with_label_returns_bytes(self):
        """Test that flash_read with label returns bytes"""
        # Mock partition info (type, subtype, offset, size, label, encrypted)
        self.mpy._mpy_comm.exec_eval.side_effect = [
            (0, 0, 0x10000, 4096, 'test', False),  # partition info
            base64.b64encode(b'\xff' * 4096).decode()  # base64 data
        ]
        result = self.mpy.flash_read(label='test')
        self.assertIsInstance(result, bytes)
        self.assertEqual(len(result), 4096)

    def test_flash_read_with_label_raises_on_not_found(self):
        """Test that flash_read with label raises error if partition not found"""
        self.mpy._mpy_comm.exec_eval.side_effect = mpy_comm.CmdError("cmd", b"", b"error")
        with self.assertRaises(mpy_comm.MpyError):
            self.mpy.flash_read(label='nonexistent')

    def test_flash_read_with_label_calls_progress_callback(self):
        """Test that progress callback is called during read"""
        self.mpy._mpy_comm.exec_eval.side_effect = [
            (0, 0, 0x10000, 4096, 'test', False),
            base64.b64encode(b'\xff' * 4096).decode()
        ]
        callback = Mock()
        self.mpy.flash_read(label='test', progress_callback=callback)
        callback.assert_called()

    def test_flash_read_with_label_requires_esp32(self):
        """Test that flash_read with label requires ESP32 platform"""
        self.mpy._platform = 'rp2'
        with self.assertRaises(mpy_comm.MpyError) as ctx:
            self.mpy.flash_read(label='test')
        self.assertIn('ESP32', str(ctx.exception))


class TestFlashWriteWithLabel(unittest.TestCase):
    """Tests for Mpy.flash_write with label (ESP32 partition)"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()
        self.mpy._platform = 'esp32'  # Set platform for partition operations
        # Mock partition info
        self.mpy._mpy_comm.exec_eval.return_value = (0, 0, 0x10000, 8192, 'test', False)
        # Mock chunk size detection
        Mpy._CHUNK_AUTO_DETECTED = 4096

    def tearDown(self):
        Mpy._CHUNK_AUTO_DETECTED = None

    def test_flash_write_with_label_returns_dict(self):
        """Test that flash_write with label returns result dict"""
        data = b'\xff' * 4096
        result = self.mpy.flash_write(data, label='test')
        self.assertIsInstance(result, dict)
        self.assertIn('size', result)
        self.assertIn('written', result)
        self.assertIn('wire_bytes', result)
        self.assertIn('compressed', result)

    def test_flash_write_with_label_raises_on_too_large(self):
        """Test that flash_write with label raises error if data too large"""
        # Partition is 8192 bytes, try to write 16384
        data = b'\xff' * 16384
        with self.assertRaises(mpy_comm.MpyError):
            self.mpy.flash_write(data, label='test')

    def test_flash_write_with_label_raises_on_not_found(self):
        """Test that flash_write with label raises error if partition not found"""
        self.mpy._mpy_comm.exec_eval.side_effect = mpy_comm.CmdError("cmd", b"", b"error")
        with self.assertRaises(mpy_comm.MpyError):
            self.mpy.flash_write(b'data', label='nonexistent')

    def test_flash_write_with_label_calls_progress_callback(self):
        """Test that progress callback is called during write"""
        data = b'\xff' * 4096
        callback = Mock()
        self.mpy.flash_write(data, label='test', progress_callback=callback)
        callback.assert_called()

    def test_flash_write_with_label_requires_esp32(self):
        """Test that flash_write with label requires ESP32 platform"""
        self.mpy._platform = 'rp2'
        with self.assertRaises(mpy_comm.MpyError) as ctx:
            self.mpy.flash_write(b'data', label='test')
        self.assertIn('ESP32', str(ctx.exception))


class TestGetcwd(unittest.TestCase):
    """Tests for Mpy.getcwd method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()

    def test_getcwd_returns_string(self):
        """Test that getcwd returns current directory string"""
        self.mpy._mpy_comm.exec_eval.return_value = '/'
        result = self.mpy.getcwd()
        self.assertEqual(result, '/')

    def test_getcwd_returns_subdirectory(self):
        """Test that getcwd returns subdirectory path"""
        self.mpy._mpy_comm.exec_eval.return_value = '/lib'
        result = self.mpy.getcwd()
        self.assertEqual(result, '/lib')

    def test_getcwd_calls_os_getcwd(self):
        """Test that getcwd calls os.getcwd()"""
        self.mpy._mpy_comm.exec_eval.return_value = '/'
        self.mpy.getcwd()
        call_args = self.mpy._mpy_comm.exec_eval.call_args[0][0]
        self.assertIn('os.getcwd()', call_args)

    def test_getcwd_imports_os(self):
        """Test that getcwd imports os module"""
        self.mpy._mpy_comm.exec_eval.return_value = '/'
        self.mpy.getcwd()
        exec_calls = [str(c) for c in self.mpy._mpy_comm.exec.call_args_list]
        os_imported = any('import os' in c for c in exec_calls)
        self.assertTrue(os_imported)


class TestChdir(unittest.TestCase):
    """Tests for Mpy.chdir method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mpy = Mpy(self.mock_conn)
        self.mpy._mpy_comm = Mock()

    def test_chdir_calls_os_chdir(self):
        """Test that chdir calls os.chdir() with path"""
        self.mpy.chdir('/lib')
        call_args = self.mpy._mpy_comm.exec.call_args_list[-1][0][0]
        self.assertIn("os.chdir('/lib')", call_args)

    def test_chdir_escapes_path(self):
        """Test that chdir escapes special characters in path"""
        self.mpy.chdir("/it's")
        call_args = self.mpy._mpy_comm.exec.call_args_list[-1][0][0]
        self.assertIn("it\\'s", call_args)

    def test_chdir_imports_os(self):
        """Test that chdir imports os module"""
        self.mpy.chdir('/lib')
        exec_calls = [str(c) for c in self.mpy._mpy_comm.exec.call_args_list]
        os_imported = any('import os' in c for c in exec_calls)
        self.assertTrue(os_imported)

    def test_chdir_raises_on_not_found(self):
        """Test that chdir raises DirNotFound on invalid path"""
        from mpytool.mpy import DirNotFound

        def exec_side_effect(cmd, *args, **kwargs):
            if 'os.chdir' in cmd:
                raise mpy_comm.CmdError("cmd", b"", b"OSError")
            return None

        self.mpy._mpy_comm.exec.side_effect = exec_side_effect
        with self.assertRaises(DirNotFound):
            self.mpy.chdir('/nonexistent')

    def test_chdir_to_root(self):
        """Test that chdir to root works"""
        self.mpy.chdir('/')
        call_args = self.mpy._mpy_comm.exec.call_args_list[-1][0][0]
        self.assertIn("os.chdir('/')", call_args)

    def test_chdir_to_relative_path(self):
        """Test that chdir to relative path works"""
        self.mpy.chdir('lib')
        call_args = self.mpy._mpy_comm.exec.call_args_list[-1][0][0]
        self.assertIn("os.chdir('lib')", call_args)

    def test_chdir_to_parent(self):
        """Test that chdir to parent directory works"""
        self.mpy.chdir('..')
        call_args = self.mpy._mpy_comm.exec.call_args_list[-1][0][0]
        self.assertIn("os.chdir('..')", call_args)


if __name__ == "__main__":
    unittest.main()
