"""Unit tests for mount module"""

import errno
import os
import struct
import tempfile
import shutil
import unittest
from unittest.mock import Mock, patch

from mpytool.mount import (
    MountHandler, ConnIntercept,
    ESCAPE, CMD_STAT, CMD_LISTDIR, CMD_OPEN, CMD_CLOSE, CMD_READ,
    CMD_WRITE, CMD_MKDIR, CMD_REMOVE,
    CMD_MIN, CMD_MAX,
)


def _pack_s32(val):
    return struct.pack('<i', val)


def _pack_u32(val):
    return struct.pack('<I', val)


def _pack_str(s):
    data = s.encode('utf-8')
    return _pack_s32(len(data)) + data


def _parse_listdir(data):
    """Parse listdir wire output into {name: mode} dict"""
    count = struct.unpack('<i', data[0:4])[0]
    if count < 0:
        return count, {}
    entries = {}
    offset = 4
    for _ in range(count):
        name_len = struct.unpack('<i', data[offset:offset + 4])[0]
        offset += 4
        name = data[offset:offset + name_len].decode()
        offset += name_len
        mode = struct.unpack('<I', data[offset:offset + 4])[0]
        offset += 4
        entries[name] = mode
    return count, entries


class MockConnForHandler:
    """Mock connection that records writes and provides reads from a queue"""

    def __init__(self):
        self._read_queue = b''
        self._written = bytearray()

    def feed(self, data):
        """Add data to the read queue"""
        self._read_queue += data

    def read_bytes(self, count, timeout=1):
        data = self._read_queue[:count]
        self._read_queue = self._read_queue[count:]
        return data

    def write(self, data):
        self._written += data

    def get_written(self):
        data = bytes(self._written)
        self._written.clear()
        return data

    def read_s8(self):
        return struct.unpack('b', self.get_written()[:1])[0]


class TestMountHandler(unittest.TestCase):
    """Tests for MountHandler file operations"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Create test file structure
        self.test_content = b'Hello MicroPython!\n'
        with open(os.path.join(self.temp_dir, 'test.py'), 'wb') as f:
            f.write(self.test_content)
        with open(os.path.join(self.temp_dir, 'data.bin'), 'wb') as f:
            f.write(b'\x00' * 1024)
        os.makedirs(os.path.join(self.temp_dir, 'lib'))
        with open(os.path.join(self.temp_dir, 'lib', 'helper.py'), 'wb') as f:
            f.write(b'# helper\n')
        self.conn = MockConnForHandler()
        self.handler = MountHandler(self.conn, self.temp_dir)

    def tearDown(self):
        self.handler.close_all()
        shutil.rmtree(self.temp_dir)

    def test_stat_file(self):
        """stat() on existing file returns mode, size, mtime"""
        self.conn.feed(_pack_str('/test.py'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, 0)  # OK
        mode = struct.unpack('<I', data[1:5])[0]
        self.assertEqual(mode, 0x8000)  # regular file
        size = struct.unpack('<I', data[5:9])[0]
        self.assertEqual(size, len(self.test_content))

    def test_stat_dir(self):
        """stat() on directory returns dir mode"""
        self.conn.feed(_pack_str('/lib'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, 0)
        mode = struct.unpack('<I', data[1:5])[0]
        self.assertEqual(mode, 0x4000)  # directory

    def test_stat_nonexistent(self):
        """stat() on nonexistent path returns ENOENT"""
        self.conn.feed(_pack_str('/nonexistent.py'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, -errno.ENOENT)

    def test_stat_path_traversal(self):
        """stat() with path traversal returns EACCES"""
        self.conn.feed(_pack_str('/../../../etc/passwd'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, -errno.EACCES)

    def test_stat_root(self):
        """stat() on root (/) returns dir mode"""
        self.conn.feed(_pack_str('/'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, 0)
        mode = struct.unpack('<I', data[1:5])[0]
        self.assertEqual(mode, 0x4000)

    def test_listdir(self):
        """listdir() returns all entries with modes and sizes"""
        self.conn.feed(_pack_str('/'))
        self.handler.dispatch(CMD_LISTDIR)
        data = self.conn.get_written()
        count, entries = _parse_listdir(data)
        self.assertEqual(count, 3)  # test.py, data.bin, lib/
        self.assertIn('test.py', entries)
        self.assertIn('data.bin', entries)
        self.assertIn('lib', entries)
        self.assertEqual(entries['test.py'], 0x8000)
        self.assertEqual(entries['lib'], 0x4000)

    def test_listdir_subdir(self):
        """listdir() on subdirectory"""
        self.conn.feed(_pack_str('/lib'))
        self.handler.dispatch(CMD_LISTDIR)
        data = self.conn.get_written()
        count = struct.unpack('<i', data[0:4])[0]
        self.assertEqual(count, 1)  # helper.py

    def test_listdir_nonexistent(self):
        """listdir() on nonexistent dir returns ENOENT"""
        self.conn.feed(_pack_str('/nope'))
        self.handler.dispatch(CMD_LISTDIR)
        data = self.conn.get_written()
        count = struct.unpack('<i', data[0:4])[0]
        self.assertEqual(count, -errno.ENOENT)

    def test_open_read_close(self):
        """Full cycle: open, read, close"""
        # Open
        self.conn.feed(_pack_str('/test.py') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertGreaterEqual(fd, 0)

        # Read
        self.conn.feed(struct.pack('b', fd) + _pack_s32(4096))
        self.handler.dispatch(CMD_READ)
        data = self.conn.get_written()
        length = struct.unpack('<i', data[0:4])[0]
        content = data[4:4 + length]
        self.assertEqual(content, self.test_content)

        # Read again (EOF)
        self.conn.feed(struct.pack('b', fd) + _pack_s32(4096))
        self.handler.dispatch(CMD_READ)
        data = self.conn.get_written()
        length = struct.unpack('<i', data[0:4])[0]
        self.assertEqual(length, 0)

        # Close
        self.conn.feed(struct.pack('b', fd))
        self.handler.dispatch(CMD_CLOSE)
        self.assertNotIn(fd, self.handler._files)

    def test_open_nonexistent(self):
        """open() on nonexistent file returns ENOENT"""
        self.conn.feed(_pack_str('/nope.py') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertEqual(fd, -errno.ENOENT)

    def test_open_path_traversal(self):
        """open() with path traversal returns EACCES"""
        self.conn.feed(_pack_str('/../../etc/passwd') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertEqual(fd, -errno.EACCES)

    def test_read_invalid_fd(self):
        """read() with invalid fd returns empty bytes"""
        self.conn.feed(struct.pack('b', 99) + _pack_s32(1024))
        self.handler.dispatch(CMD_READ)
        data = self.conn.get_written()
        length = struct.unpack('<i', data[0:4])[0]
        self.assertEqual(length, 0)

    def test_close_all(self):
        """close_all() closes all open files"""
        # Open two files
        self.conn.feed(_pack_str('/test.py') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        self.conn.get_written()

        self.conn.feed(_pack_str('/data.bin') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        self.conn.get_written()

        self.assertEqual(len(self.handler._files), 2)
        self.handler.close_all()
        self.assertEqual(len(self.handler._files), 0)

    def test_multiple_fd(self):
        """Opening multiple files assigns different fds"""
        # Open first
        self.conn.feed(_pack_str('/test.py') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        fd1 = struct.unpack('b', self.conn.get_written()[:1])[0]

        # Open second
        self.conn.feed(_pack_str('/data.bin') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        fd2 = struct.unpack('b', self.conn.get_written()[:1])[0]

        self.assertNotEqual(fd1, fd2)


class TestMountHandlerSubmount(unittest.TestCase):
    """Tests for MountHandler virtual submount support"""

    def setUp(self):
        self.temp_root = tempfile.mkdtemp()
        self.temp_sub = tempfile.mkdtemp()
        # Root has test.py
        with open(os.path.join(self.temp_root, 'test.py'), 'wb') as f:
            f.write(b'# root\n')
        # Submount has helper.py
        with open(os.path.join(self.temp_sub, 'helper.py'), 'wb') as f:
            f.write(b'# submount\n')
        os.makedirs(os.path.join(self.temp_sub, 'subdir'))
        self.conn = MockConnForHandler()
        self.handler = MountHandler(self.conn, self.temp_root)
        self.handler.add_submount('lib', self.temp_sub)

    def tearDown(self):
        self.handler.close_all()
        shutil.rmtree(self.temp_root)
        shutil.rmtree(self.temp_sub)

    def test_stat_submount_file(self):
        """stat on submount file resolves to submount dir"""
        self.conn.feed(_pack_str('/lib/helper.py'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, 0)  # OK
        mode = struct.unpack('<I', data[1:5])[0]
        self.assertEqual(mode, 0x8000)  # regular file

    def test_stat_submount_dir(self):
        """stat on submount path itself returns directory"""
        self.conn.feed(_pack_str('/lib'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, 0)
        mode = struct.unpack('<I', data[1:5])[0]
        self.assertEqual(mode, 0x4000)  # directory

    def test_stat_root_file(self):
        """stat on root file still works with submount present"""
        self.conn.feed(_pack_str('/test.py'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, 0)

    def test_listdir_root_includes_virtual(self):
        """listdir root includes virtual submount directory"""
        self.conn.feed(_pack_str('/'))
        self.handler.dispatch(CMD_LISTDIR)
        count, entries = _parse_listdir(self.conn.get_written())
        self.assertIn('test.py', entries)
        self.assertIn('lib', entries)  # virtual dir injected

    def test_listdir_submount(self):
        """listdir on submount path lists submount contents"""
        self.conn.feed(_pack_str('/lib'))
        self.handler.dispatch(CMD_LISTDIR)
        count, entries = _parse_listdir(self.conn.get_written())
        self.assertIn('helper.py', entries)
        self.assertIn('subdir', entries)

    def test_open_read_submount(self):
        """Open and read file from submount"""
        self.conn.feed(_pack_str('/lib/helper.py') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertGreaterEqual(fd, 0)
        # Read
        self.conn.feed(struct.pack('b', fd) + _pack_s32(4096))
        self.handler.dispatch(CMD_READ)
        data = self.conn.get_written()
        length = struct.unpack('<i', data[0:4])[0]
        content = data[4:4 + length]
        self.assertEqual(content, b'# submount\n')

    def test_submount_traversal_blocked(self):
        """Path traversal out of submount is blocked"""
        self.conn.feed(
            _pack_str('/lib/../../etc/passwd') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertEqual(fd, -errno.EACCES)

    def test_listdir_no_duplicate_when_real_exists(self):
        """Submount dir not duplicated if real dir with same name exists"""
        # Create real 'lib' dir in root
        os.makedirs(os.path.join(self.temp_root, 'lib'), exist_ok=True)
        self.conn.feed(_pack_str('/'))
        self.handler.dispatch(CMD_LISTDIR)
        count, entries = _parse_listdir(self.conn.get_written())
        names = list(entries.keys())
        # 'lib' should appear only once
        self.assertEqual(names.count('lib'), 1)


class TestMountHandlerVirtualDir(unittest.TestCase):
    """Tests for virtual intermediate directories from submounts"""

    def setUp(self):
        self.temp_root = tempfile.mkdtemp()
        self.temp_pkg = tempfile.mkdtemp()
        self.temp_file_dir = tempfile.mkdtemp()
        # Root has root.txt only (no 'lib' directory)
        with open(os.path.join(self.temp_root, 'root.txt'), 'wb') as f:
            f.write(b'root\n')
        # Package dir has mod.py
        with open(os.path.join(self.temp_pkg, 'mod.py'), 'wb') as f:
            f.write(b'# mod\n')
        # Single file
        self.single_file = os.path.join(self.temp_file_dir, 'single.py')
        with open(self.single_file, 'wb') as f:
            f.write(b'# single\n')
        self.conn = MockConnForHandler()
        self.handler = MountHandler(self.conn, self.temp_root)
        # Submounts create virtual 'lib' intermediate directory
        self.handler.add_submount('lib/pkg', self.temp_pkg)
        self.handler.add_submount('lib/single.py', self.single_file)

    def tearDown(self):
        self.handler.close_all()
        shutil.rmtree(self.temp_root)
        shutil.rmtree(self.temp_pkg)
        shutil.rmtree(self.temp_file_dir)

    def test_stat_virtual_dir(self):
        """stat on virtual intermediate dir returns S_IFDIR"""
        self.conn.feed(_pack_str('/lib'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, 0)  # OK
        mode = struct.unpack('<I', data[1:5])[0]
        self.assertEqual(mode, 0x4000)

    def test_listdir_virtual_dir(self):
        """listdir on virtual intermediate dir returns submount entries"""
        self.conn.feed(_pack_str('/lib'))
        self.handler.dispatch(CMD_LISTDIR)
        count, entries = _parse_listdir(self.conn.get_written())
        self.assertEqual(count, 2)
        self.assertIn('pkg', entries)
        self.assertIn('single.py', entries)
        self.assertEqual(entries['pkg'], 0x4000)  # directory
        self.assertEqual(entries['single.py'], 0x8000)  # file

    def test_listdir_root_shows_virtual(self):
        """listdir root includes virtual 'lib' directory"""
        self.conn.feed(_pack_str('/'))
        self.handler.dispatch(CMD_LISTDIR)
        count, entries = _parse_listdir(self.conn.get_written())
        self.assertIn('root.txt', entries)
        self.assertIn('lib', entries)

    def test_stat_nonexistent_in_virtual(self):
        """stat on nonexistent path under virtual dir returns ENOENT"""
        self.conn.feed(_pack_str('/lib/nope'))
        self.handler.dispatch(CMD_STAT)
        data = self.conn.get_written()
        result = struct.unpack('b', data[0:1])[0]
        self.assertEqual(result, -errno.ENOENT)

    def test_open_file_submount(self):
        """open and read single file submount"""
        self.conn.feed(_pack_str('/lib/single.py') + _pack_str('rb'))
        self.handler.dispatch(CMD_OPEN)
        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertGreaterEqual(fd, 0)
        # Read
        self.conn.feed(struct.pack('b', fd) + _pack_s32(4096))
        self.handler.dispatch(CMD_READ)
        data = self.conn.get_written()
        length = struct.unpack('<i', data[0:4])[0]
        content = data[4:4 + length]
        self.assertEqual(content, b'# single\n')


class TestConnIntercept(unittest.TestCase):
    """Tests for ConnIntercept transparent proxy"""

    def setUp(self):
        self.mock_conn = Mock()
        self.mock_conn.fd = 5
        self.mock_handler = Mock()

    def _make_intercept(self, remount_fn=None):
        return ConnIntercept(
            self.mock_conn, {0: self.mock_handler},
            remount_fn=remount_fn)

    def test_fd_delegation(self):
        """fd property delegates to underlying connection"""
        intercept = self._make_intercept()
        self.assertEqual(intercept.fd, 5)

    def test_passthrough_no_escape(self):
        """Data without 0x18 passes through unchanged"""
        intercept = self._make_intercept()
        self.mock_conn._read_available.return_value = b'Hello World\r\n'
        result = intercept._read_available()
        self.assertEqual(result, b'Hello World\r\n')
        self.mock_handler.dispatch.assert_not_called()

    def test_passthrough_none(self):
        """None from underlying conn passes through"""
        intercept = self._make_intercept()
        self.mock_conn._read_available.return_value = None
        result = intercept._read_available()
        self.assertIsNone(result)

    def test_intercept_vfs_command(self):
        """0x18 + valid CMD triggers dispatch"""
        intercept = self._make_intercept()
        # VFS command in stream: ESCAPE + CMD_STAT + MID=0
        self.mock_conn._read_available.return_value = bytes([ESCAPE, CMD_STAT, 0])
        result = intercept._read_available()
        # ACK sent
        self.mock_conn.write.assert_called_once_with(bytes([ESCAPE]))
        # Handler dispatched
        self.mock_handler.dispatch.assert_called_once_with(CMD_STAT)
        # No REPL output (VFS command consumed), but returns b'' to signal processing occurred
        self.assertEqual(result, b'')

    def test_mixed_data(self):
        """VFS command embedded in REPL output"""
        intercept = self._make_intercept()
        data = b'Hello' + bytes([ESCAPE, CMD_READ, 0]) + b'World'
        self.mock_conn._read_available.return_value = data
        result = intercept._read_available()
        self.assertEqual(result, b'HelloWorld')
        self.mock_handler.dispatch.assert_called_once_with(CMD_READ)

    def test_partial_escape_at_end(self):
        """0x18 at end of buffer saved as pending"""
        intercept = self._make_intercept()
        # Only escape byte, no CMD yet
        self.mock_conn._read_available.return_value = b'data' + bytes([ESCAPE])
        result = intercept._read_available()
        self.assertEqual(result, b'data')
        self.assertEqual(intercept._pending, bytes([ESCAPE]))
        self.mock_handler.dispatch.assert_not_called()

    def test_partial_escape_one_byte(self):
        """0x18 + CMD but no MID saved as pending"""
        intercept = self._make_intercept()
        self.mock_conn._read_available.return_value = bytes([ESCAPE, CMD_STAT])
        result = intercept._read_available()
        self.assertEqual(result, b'')  # Got new data, no REPL output
        self.assertEqual(intercept._pending, bytes([ESCAPE, CMD_STAT]))

    def test_pending_completed_next_read(self):
        """Pending bytes completed on next read"""
        intercept = self._make_intercept()
        # First read: partial escape
        self.mock_conn._read_available.return_value = bytes([ESCAPE, CMD_STAT])
        intercept._read_available()
        # Second read: MID arrives
        self.mock_conn._read_available.return_value = bytes([0])
        intercept._read_available()
        self.mock_handler.dispatch.assert_called_once_with(CMD_STAT)

    def test_invalid_cmd_passthrough(self):
        """0x18 + invalid CMD passes through as data"""
        intercept = self._make_intercept()
        # 0x18 followed by 0xFF (not a valid command)
        self.mock_conn._read_available.return_value = bytes([ESCAPE, 0xFF, 0x00])
        result = intercept._read_available()
        # 0x18 passes through, rest continues as normal data
        self.assertIn(ESCAPE, result)

    def test_has_data_with_pending(self):
        """_has_data returns True when pending data exists"""
        intercept = self._make_intercept()
        intercept._pending = b'pending'
        self.mock_conn._has_data.return_value = False
        self.assertTrue(intercept._has_data(0))

    def test_has_data_delegates(self):
        """_has_data delegates to underlying conn when no pending"""
        intercept = self._make_intercept()
        self.mock_conn._has_data.return_value = True
        self.assertTrue(intercept._has_data(0.1))
        self.mock_conn._has_data.assert_called_with(0.1)

    def test_write_delegates(self):
        """_write_raw delegates to underlying connection"""
        intercept = self._make_intercept()
        self.mock_conn._write_raw.return_value = 5
        result = intercept._write_raw(b'hello')
        self.assertEqual(result, 5)
        self.mock_conn._write_raw.assert_called_with(b'hello')

    def test_close_closes_all_handlers(self):
        """close() closes all handlers and underlying connection"""
        intercept = self._make_intercept()
        intercept.close()
        self.mock_handler.close_all.assert_called_once()
        self.mock_conn.close.assert_called_once()

    def test_multiple_vfs_commands(self):
        """Multiple VFS commands in single read"""
        intercept = self._make_intercept()
        data = (bytes([ESCAPE, CMD_STAT, 0])
                + bytes([ESCAPE, CMD_OPEN, 0]))
        self.mock_conn._read_available.return_value = data
        intercept._read_available()
        self.assertEqual(self.mock_handler.dispatch.call_count, 2)


class TestConnInterceptSoftReboot(unittest.TestCase):
    """Tests for soft reboot detection in ConnIntercept"""

    def test_soft_reboot_detection(self):
        """Detects 'soft reboot' in stream"""
        remount = Mock()
        mock_conn = Mock()
        mock_conn.fd = 5
        handler = Mock()
        intercept = ConnIntercept(
            mock_conn, {0: handler}, remount_fn=remount)

        # Send "MPY: soft reboot\r\n"
        mock_conn._read_available.return_value = b'MPY: soft reboot\r\n'
        intercept._read_available()
        self.assertTrue(intercept._needs_remount)
        remount.assert_not_called()

        # Send REPL prompt
        mock_conn._read_available.return_value = b'MicroPython v1.25\r\n>>> '
        intercept._read_available()
        remount.assert_called_once()
        self.assertFalse(intercept._needs_remount)

    def test_no_remount_without_callback(self):
        """No remount if remount_fn is None"""
        mock_conn = Mock()
        mock_conn.fd = 5
        handler = Mock()
        intercept = ConnIntercept(
            mock_conn, {0: handler}, remount_fn=None)

        mock_conn._read_available.return_value = b'MPY: soft reboot\r\n'
        intercept._read_available()
        self.assertFalse(intercept._needs_remount)

    def test_reboot_split_across_reads(self):
        """Soft reboot text split across multiple reads"""
        remount = Mock()
        mock_conn = Mock()
        mock_conn.fd = 5
        handler = Mock()
        intercept = ConnIntercept(
            mock_conn, {0: handler}, remount_fn=remount)

        mock_conn._read_available.return_value = b'MPY: soft reb'
        intercept._read_available()
        self.assertFalse(intercept._needs_remount)

        mock_conn._read_available.return_value = b'oot\r\n'
        intercept._read_available()
        self.assertTrue(intercept._needs_remount)


class TestMountAutoRepl(unittest.TestCase):
    """Tests for auto-REPL logic with mount command"""

    def test_mount_only(self):
        """mount without repl/monitor appends repl"""
        from mpytool.mpytool import _mount_auto_repl
        groups = [['mount', './src']]
        _mount_auto_repl(groups)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[1], ['repl'])

    def test_mount_with_repl(self):
        """mount -- repl does not append extra repl"""
        from mpytool.mpytool import _mount_auto_repl
        groups = [['mount', './src'], ['repl']]
        _mount_auto_repl(groups)
        self.assertEqual(len(groups), 2)

    def test_mount_with_monitor(self):
        """mount -- monitor does not append repl"""
        from mpytool.mpytool import _mount_auto_repl
        groups = [['mount', './src'], ['monitor']]
        _mount_auto_repl(groups)
        self.assertEqual(len(groups), 2)

    def test_mount_with_exec(self):
        """mount -- exec "..." appends repl"""
        from mpytool.mpytool import _mount_auto_repl
        groups = [['mount', './src'], ['exec', 'import app']]
        _mount_auto_repl(groups)
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[2], ['repl'])

    def test_mount_exec_monitor(self):
        """mount -- exec -- monitor does not append repl"""
        from mpytool.mpytool import _mount_auto_repl
        groups = [['mount', './src'], ['exec', 'import app'], ['monitor']]
        _mount_auto_repl(groups)
        self.assertEqual(len(groups), 3)

    def test_no_mount(self):
        """Without mount, no auto-repl"""
        from mpytool.mpytool import _mount_auto_repl
        groups = [['exec', 'print(1)']]
        _mount_auto_repl(groups)
        self.assertEqual(len(groups), 1)

    def test_mount_list_no_auto_repl(self):
        """mount without args (listing) does not append repl"""
        from mpytool.mpytool import _mount_auto_repl
        groups = [['mount']]
        _mount_auto_repl(groups)
        self.assertEqual(len(groups), 1)


class TestMountDispatch(unittest.TestCase):
    """Tests for mount command dispatch"""

    def setUp(self):
        from mpytool.mpytool import MpyTool
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()
        self.tool._mpy.is_submount.return_value = False

    def test_mount_missing_dir(self):
        """mount with nonexistent directory raises ParamsError"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_mount(
                ['/nonexistent/path'], is_last_group=True)
        self.assertIn('not found', str(ctx.exception))

    def test_mount_no_args_lists(self):
        """mount without args calls list_mounts"""
        self.tool._mpy.list_mounts.return_value = []
        with patch('builtins.print'):
            self.tool._dispatch_mount([], is_last_group=True)
        self.tool._mpy.list_mounts.assert_called_once()

    def test_mount_invalid_mountpoint(self):
        """mount with relative mount point raises ParamsError"""
        from mpytool.mpytool import ParamsError
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ParamsError) as ctx:
                self.tool._dispatch_mount(
                    [d, ':relative'], is_last_group=True)
            self.assertIn('absolute', str(ctx.exception))

    def test_mount_calls_mpy(self):
        """mount calls mpy.mount with correct args"""
        with tempfile.TemporaryDirectory() as d:
            self.tool._dispatch_mount([d], is_last_group=False)
            self.tool._mpy.mount.assert_called_once_with(
                d, '/remote', log=self.tool._log,
                writable=False, mpy_cross=None)

    def test_mount_custom_mountpoint(self):
        """mount with custom mount point"""
        with tempfile.TemporaryDirectory() as d:
            self.tool._dispatch_mount(
                [d, ':/app'], is_last_group=False)
            self.tool._mpy.mount.assert_called_once_with(
                d, '/app', log=self.tool._log,
                writable=False, mpy_cross=None)

    def test_mount_in_commands(self):
        """mount is in _COMMANDS set"""
        from mpytool.mpytool import MpyTool
        self.assertIn('mount', MpyTool._COMMANDS)

    def test_mount_multi_pair(self):
        """mount with multiple dir:mp pairs"""
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                self.tool._dispatch_mount(
                    [d1, ':/app', d2, ':/lib'],
                    is_last_group=False)
                calls = self.tool._mpy.mount.call_args_list
                self.assertEqual(len(calls), 2)
                self.assertEqual(
                    calls[0].args, (d1, '/app'))
                self.assertEqual(
                    calls[1].args, (d2, '/lib'))

    def test_mount_duplicate_mountpoints(self):
        """mount with duplicate mount points raises ParamsError"""
        from mpytool.mpytool import ParamsError
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                with self.assertRaises(ParamsError) as ctx:
                    self.tool._dispatch_mount(
                        [d1, ':/app', d2, ':/app'],
                        is_last_group=False)
                self.assertIn('duplicate', str(ctx.exception))

    def test_mount_multi_default_mountpoints(self):
        """mount multiple dirs without explicit mps = duplicate /remote"""
        from mpytool.mpytool import ParamsError
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                with self.assertRaises(ParamsError) as ctx:
                    self.tool._dispatch_mount(
                        [d1, d2], is_last_group=False)
                self.assertIn('duplicate', str(ctx.exception))


class TestMountNested(unittest.TestCase):
    """Tests for nested mount points (virtual submounts)"""

    def setUp(self):
        from mpytool.mpy import Mpy
        self.mock_conn = Mock()
        self.mock_conn._buffer = bytearray()
        self.mock_conn._has_data.return_value = False
        self.mpy = Mpy(self.mock_conn)

    def test_nested_mount_raises(self):
        """Nested mount raises error"""
        from mpytool.mpy_comm import MpyError
        self.mpy._mounts = [(0, '/xyz', '/tmp/a', Mock())]
        self.mpy._intercept = Mock()
        with self.assertRaises(MpyError) as ctx:
            self.mpy.mount('/tmp/b', '/xyz/lib')
        self.assertIn('nested', str(ctx.exception))

    def test_existing_nested_inside_new_raises(self):
        """Cannot mount if existing mount would be nested inside new"""
        from mpytool.mpy_comm import MpyError
        self.mpy._mounts = [(0, '/xyz/lib', '/tmp/a', Mock())]
        self.mpy._intercept = Mock()
        with self.assertRaises(MpyError) as ctx:
            self.mpy.mount('/tmp/b', '/xyz')
        self.assertIn('nested', str(ctx.exception))

    def test_sibling_mounts_ok(self):
        """Sibling mount points (non-nested) pass validation"""
        from mpytool.mpy_comm import MpyError
        self.mpy._mounts = [(0, '/app', '/tmp/a', Mock())]
        self.mpy._intercept = Mock()
        try:
            self.mpy.mount('/tmp/b', '/lib')
        except MpyError as e:
            msg = str(e)
            if 'inside' in msg or 'nested' in msg:
                self.fail(f"Unexpected nested mount error: {e}")
        except Exception:
            pass  # other errors expected (mock not fully set up)

    def test_deep_nested_mount_raises(self):
        """Deeply nested mount raises error"""
        from mpytool.mpy_comm import MpyError
        self.mpy._mounts = [(0, '/xyz', '/tmp/a', Mock())]
        self.mpy._intercept = Mock()
        with self.assertRaises(MpyError) as ctx:
            self.mpy.mount('/tmp/deep', '/xyz/lib/vendor')
        self.assertIn('nested', str(ctx.exception))


class TestConnInterceptMultiHandler(unittest.TestCase):
    """Tests for ConnIntercept multi-handler dispatch"""

    def test_dispatch_to_correct_handler(self):
        """VFS command routed to handler matching mid"""
        mock_conn = Mock()
        mock_conn.fd = 5
        handler0 = Mock()
        handler1 = Mock()
        intercept = ConnIntercept(
            mock_conn, {0: handler0, 1: handler1})

        # Send command with mid=1
        mock_conn._read_available.return_value = bytes(
            [ESCAPE, CMD_STAT, 1])
        intercept._read_available()
        handler0.dispatch.assert_not_called()
        handler1.dispatch.assert_called_once_with(CMD_STAT)

    def test_dispatch_mid0(self):
        """VFS command with mid=0 goes to handler 0"""
        mock_conn = Mock()
        mock_conn.fd = 5
        handler0 = Mock()
        handler1 = Mock()
        intercept = ConnIntercept(
            mock_conn, {0: handler0, 1: handler1})

        mock_conn._read_available.return_value = bytes(
            [ESCAPE, CMD_OPEN, 0])
        intercept._read_available()
        handler0.dispatch.assert_called_once_with(CMD_OPEN)
        handler1.dispatch.assert_not_called()

    def test_unknown_mid_ignored(self):
        """VFS command with unknown mid is consumed but not dispatched"""
        mock_conn = Mock()
        mock_conn.fd = 5
        handler0 = Mock()
        intercept = ConnIntercept(mock_conn, {0: handler0})

        # mid=5 has no handler
        mock_conn._read_available.return_value = bytes(
            [ESCAPE, CMD_STAT, 5])
        result = intercept._read_available()
        handler0.dispatch.assert_not_called()
        # ACK not sent either (no handler)
        mock_conn.write.assert_not_called()
        self.assertEqual(result, b'')  # Got new data, no REPL output

    def test_mixed_mids_in_stream(self):
        """Multiple commands with different mids in one read"""
        mock_conn = Mock()
        mock_conn.fd = 5
        handler0 = Mock()
        handler1 = Mock()
        intercept = ConnIntercept(
            mock_conn, {0: handler0, 1: handler1})

        data = (bytes([ESCAPE, CMD_STAT, 0])
                + bytes([ESCAPE, CMD_OPEN, 1])
                + b'output')
        mock_conn._read_available.return_value = data
        result = intercept._read_available()
        handler0.dispatch.assert_called_once_with(CMD_STAT)
        handler1.dispatch.assert_called_once_with(CMD_OPEN)
        self.assertEqual(result, b'output')

    def test_add_handler(self):
        """add_handler adds new mid routing"""
        mock_conn = Mock()
        mock_conn.fd = 5
        handler0 = Mock()
        intercept = ConnIntercept(mock_conn, {0: handler0})

        handler1 = Mock()
        intercept.add_handler(1, handler1)

        mock_conn._read_available.return_value = bytes(
            [ESCAPE, CMD_READ, 1])
        intercept._read_available()
        handler1.dispatch.assert_called_once_with(CMD_READ)

    def test_close_all_handlers(self):
        """close() calls close_all on every handler"""
        mock_conn = Mock()
        mock_conn.fd = 5
        handler0 = Mock()
        handler1 = Mock()
        intercept = ConnIntercept(
            mock_conn, {0: handler0, 1: handler1})

        intercept.close()
        handler0.close_all.assert_called_once()
        handler1.close_all.assert_called_once()
        mock_conn.close.assert_called_once()


class TestStaleVfsRecovery(unittest.TestCase):
    """Tests for stale VFS recovery in stop_current_operation"""

    def test_stop_sends_escape_after_failures(self):
        """stop_current_operation sends 0x18 after attempt 4"""
        from mpytool.mpy_comm import MpyComm
        from mpytool.conn import Timeout

        mock_conn = Mock()
        mock_conn.flush.return_value = b''
        mock_conn.read_until.side_effect = Timeout("no data")

        comm = MpyComm(mock_conn)
        comm.stop_current_operation()

        writes = [call.args[0] for call in mock_conn.write.call_args_list]
        escape_writes = [w for w in writes if w == b'\x18']
        self.assertGreater(len(escape_writes), 0)

    def test_stop_no_escape_in_early_attempts(self):
        """First 4 attempts don't send 0x18"""
        from mpytool.mpy_comm import MpyComm
        from mpytool.conn import Timeout

        mock_conn = Mock()
        mock_conn.flush.return_value = b''

        attempt_count = 0

        def read_side_effect(*args, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count <= 4:
                writes = [
                    c.args[0] for c in mock_conn.write.call_args_list]
                escape_writes = [w for w in writes if w == b'\x18']
                assert len(escape_writes) == 0, \
                    f"0x18 sent too early at attempt {attempt_count}"
            raise Timeout("no data")

        mock_conn.read_until.side_effect = read_side_effect
        comm = MpyComm(mock_conn)
        comm.stop_current_operation()


class TestStaleVfsCleanup(unittest.TestCase):
    """Tests for _cleanup_stale_vfs in Mpy"""

    def test_cleanup_runs_on_first_import(self):
        """_cleanup_stale_vfs called on first import_module"""
        from mpytool.mpy import Mpy

        mock_conn = Mock()
        mpy = Mpy(mock_conn)
        mpy._mpy_comm = Mock()
        mpy._mpy_comm.exec.return_value = b''

        mpy.import_module('os')

        # First call should be cleanup, second should be import
        calls = mpy._mpy_comm.exec.call_args_list
        self.assertEqual(len(calls), 2)
        cleanup_code = calls[0].args[0]
        self.assertIn('uos.umount', cleanup_code)
        self.assertIn('uos.statvfs', cleanup_code)
        import_code = calls[1].args[0]
        self.assertEqual(import_code, 'import os')

    def test_cleanup_runs_only_once(self):
        """_cleanup_stale_vfs not called on subsequent imports"""
        from mpytool.mpy import Mpy

        mock_conn = Mock()
        mpy = Mpy(mock_conn)
        mpy._mpy_comm = Mock()
        mpy._mpy_comm.exec.return_value = b''

        mpy.import_module('os')
        mpy.import_module('gc')

        calls = mpy._mpy_comm.exec.call_args_list
        # cleanup + import os + import gc = 3
        self.assertEqual(len(calls), 3)
        # Only first is cleanup
        self.assertIn('uos.umount', calls[0].args[0])
        self.assertEqual(calls[1].args[0], 'import os')
        self.assertEqual(calls[2].args[0], 'import gc')

    def test_cleanup_skipped_with_active_mount(self):
        """_cleanup_stale_vfs skipped when intercept is active"""
        from mpytool.mpy import Mpy

        mock_conn = Mock()
        mpy = Mpy(mock_conn)
        mpy._mpy_comm = Mock()
        mpy._mpy_comm.exec.return_value = b''
        mpy._intercept = Mock()  # Active mount

        mpy.import_module('os')

        calls = mpy._mpy_comm.exec.call_args_list
        # Only import, no cleanup
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], 'import os')

    def test_cleanup_error_ignored(self):
        """_cleanup_stale_vfs errors don't block import"""
        from mpytool.mpy import Mpy
        from mpytool.conn import Timeout

        mock_conn = Mock()
        mpy = Mpy(mock_conn)
        mpy._mpy_comm = Mock()

        call_count = 0

        def exec_side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Timeout("cleanup failed")
            return b''

        mpy._mpy_comm.exec.side_effect = exec_side_effect

        # Should not raise
        mpy.import_module('os')
        self.assertEqual(call_count, 2)

    def test_reset_state_clears_cleanup_flag(self):
        """reset_state() allows cleanup to run again"""
        from mpytool.mpy import Mpy

        mock_conn = Mock()
        mpy = Mpy(mock_conn)
        mpy._stale_cleanup_done = True
        mpy.reset_state()
        self.assertFalse(mpy._stale_cleanup_done)


class TestLnDispatch(unittest.TestCase):
    """Tests for ln command dispatch"""

    def setUp(self):
        from mpytool.mpytool import MpyTool
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()
        self.mock_handler = Mock()
        # Simulate active mount at /app
        self.tool._mpy._mounts = [
            (0, '/app', '/tmp/src', self.mock_handler)]
        self.tool._mpy.add_submount = Mock()
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, 'file.py')
        with open(self.temp_file, 'w') as f:
            f.write('# test')
        self.temp_sub = os.path.join(self.temp_dir, 'pkg')
        os.makedirs(self.temp_sub)

    def tearDown(self):
        import gc
        gc.collect()  # Force cleanup of handlers on Windows
        shutil.rmtree(self.temp_dir)

    def test_ln_dir(self):
        """ln ./dir :/app/lib -> subpath 'lib'"""
        self.tool._dispatch_ln(
            [self.temp_sub, ':/app/lib'], is_last_group=True)
        self.tool._mpy.add_submount.assert_called_once_with(
            '/app', 'lib', self.temp_sub)

    def test_ln_dir_trailing_slash_dst(self):
        """ln ./dir :/app/lib/ -> subpath 'lib/pkg'"""
        self.tool._dispatch_ln(
            [self.temp_sub, ':/app/lib/'], is_last_group=True)
        self.tool._mpy.add_submount.assert_called_once_with(
            '/app', 'lib/pkg', self.temp_sub)

    def test_ln_dir_contents(self):
        """ln ./dir/ :/app/lib/ -> subpath 'lib' (contents)"""
        self.tool._dispatch_ln(
            [self.temp_sub + '/', ':/app/lib/'], is_last_group=True)
        self.tool._mpy.add_submount.assert_called_once_with(
            '/app', 'lib', self.temp_sub)

    def test_ln_file(self):
        """ln ./file.py :/app/file.py -> subpath 'file.py'"""
        self.tool._dispatch_ln(
            [self.temp_file, ':/app/file.py'], is_last_group=True)
        self.tool._mpy.add_submount.assert_called_once_with(
            '/app', 'file.py', self.temp_file)

    def test_ln_file_to_dir(self):
        """ln ./file.py :/app/lib/ -> subpath 'lib/file.py'"""
        self.tool._dispatch_ln(
            [self.temp_file, ':/app/lib/'], is_last_group=True)
        self.tool._mpy.add_submount.assert_called_once_with(
            '/app', 'lib/file.py', self.temp_file)

    def test_ln_multi_src(self):
        """ln ./a.py ./pkg :/app/lib/ -> two add_submount calls"""
        self.tool._dispatch_ln(
            [self.temp_file, self.temp_sub, ':/app/lib/'],
            is_last_group=True)
        calls = self.tool._mpy.add_submount.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0].args, ('/app', 'lib/file.py', self.temp_file))
        self.assertEqual(
            calls[1].args, ('/app', 'lib/pkg', self.temp_sub))

    def test_ln_multi_src_no_trailing_slash_error(self):
        """ln ./a ./b :/lib -> error (no trailing /)"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_ln(
                [self.temp_file, self.temp_sub, ':/app/lib'],
                is_last_group=True)
        self.assertIn('directory destination', str(ctx.exception))

    def test_ln_contents_no_trailing_slash_error(self):
        """ln ./dir/ :/lib -> error (contents without trailing /)"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_ln(
                [self.temp_sub + '/', ':/app/lib'],
                is_last_group=True)
        self.assertIn('directory', str(ctx.exception))

    def test_ln_no_mount_error(self):
        """ln without active mount -> error"""
        from mpytool.mpytool import ParamsError
        self.tool._mpy._mounts = []
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_ln(
                [self.temp_file, ':/lib/file.py'], is_last_group=True)
        self.assertIn('mount', str(ctx.exception))

    def test_ln_no_args_error(self):
        """ln without arguments -> error"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError):
            self.tool._dispatch_ln([], is_last_group=True)

    def test_ln_nonexistent_source_error(self):
        """ln with nonexistent source -> error"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_ln(
                ['/nonexistent/path', ':/app/lib/'],
                is_last_group=True)
        self.assertIn('not found', str(ctx.exception))

    def test_ln_in_commands(self):
        """ln is in _COMMANDS set"""
        from mpytool.mpytool import MpyTool
        self.assertIn('ln', MpyTool._COMMANDS)

    def test_ln_dst_without_colon_error(self):
        """ln dst without : prefix -> error"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_ln(
                [self.temp_file, '/app/lib/'], is_last_group=True)
        self.assertIn(': prefix', str(ctx.exception))

    def test_ln_dst_relative_path_error(self):
        """ln dst with relative path -> error"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_ln(
                [self.temp_file, ':lib/'], is_last_group=True)
        self.assertIn('absolute', str(ctx.exception))

    def test_ln_root_mount(self):
        """ln with mount at / works correctly"""
        self.tool._mpy._mounts = [
            (0, '/', '/tmp/src', self.mock_handler)]
        self.tool._dispatch_ln(
            [self.temp_sub, ':/lib/'], is_last_group=True)
        self.tool._mpy.add_submount.assert_called_once_with(
            '/', 'lib/pkg', self.temp_sub)


class TestMountHandlerMpyCross(unittest.TestCase):
    """Tests for MountHandler with mpy-cross compilation (-m flag)"""

    def setUp(self):
        self.temp_dir = os.path.realpath(tempfile.mkdtemp())
        self.cache_dir = os.path.join(self.temp_dir, '__pycache__')
        os.makedirs(self.cache_dir)

        # Create test files
        self.test_py = os.path.join(self.temp_dir, 'test.py')
        with open(self.test_py, 'w') as f:
            f.write('print("hello")\n')

        self.boot_py = os.path.join(self.temp_dir, 'boot.py')
        with open(self.boot_py, 'w') as f:
            f.write('# boot file\n')

        self.empty_py = os.path.join(self.temp_dir, 'empty.py')
        with open(self.empty_py, 'w') as f:
            pass

        # Mock connection
        self.conn = MockConnForHandler()

        # Mock MpyCross
        self.mpy_cross = Mock()
        self.mpy_cross.compiled = {}

        # Create handler with mpy_cross
        self.handler = MountHandler(
            self.conn, self.temp_dir, mpy_cross=self.mpy_cross)

    def tearDown(self):
        self.handler.close_all()
        shutil.rmtree(self.temp_dir)

    def test_stat_boot_py_always_normal(self):
        """stat('boot.py') always returns normal stat, never redirects"""
        self.mpy_cross.compile.return_value = '/cache/boot.mpy-6.3.mpy'

        self.conn.feed(_pack_str('/boot.py'))
        self.handler._do_stat()

        # Should return OK + stat, not ENOENT
        result = bytes(self.conn._written)
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK
        # Should NOT call compile for boot files
        self.mpy_cross.compile.assert_not_called()

    def test_stat_empty_py_no_compile(self):
        """stat('empty.py') with size=0 returns normal stat, no compile"""
        self.conn.feed(_pack_str('/empty.py'))
        self.handler._do_stat()

        # Should return OK + stat with size=0
        result = bytes(self.conn._written)
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK
        size = struct.unpack('<I', result[5:9])[0]
        self.assertEqual(size, 0)
        # Should NOT call compile for empty files
        self.mpy_cross.compile.assert_not_called()

    def test_stat_py_compile_success_redirects(self):
        """stat('test.py') with successful compile returns ENOENT (redirect)"""
        cache_path = os.path.join(self.cache_dir, 'test.mpy-6.3.mpy')
        with open(cache_path, 'wb') as f:
            f.write(b'MPY\x06\x03...')  # fake .mpy
        self.mpy_cross.compile.return_value = cache_path

        self.conn.feed(_pack_str('/test.py'))
        self.handler._do_stat()

        # Should return ENOENT to force MicroPython to try .mpy
        result = bytes(self.conn._written)
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, -errno.ENOENT)
        self.mpy_cross.compile.assert_called_once_with(self.test_py)

    def test_stat_py_compile_fail_fallback(self):
        """stat('test.py') with failed compile returns normal stat"""
        self.mpy_cross.compile.return_value = None  # compile failed

        self.conn.feed(_pack_str('/test.py'))
        self.handler._do_stat()

        # Should return OK + normal .py stat
        result = bytes(self.conn._written)
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK
        size = struct.unpack('<I', result[5:9])[0]
        self.assertGreater(size, 0)
        self.mpy_cross.compile.assert_called_once_with(self.test_py)

    def test_stat_mpy_prebuilt_found(self):
        """stat('test.mpy') finds prebuilt .mpy in same directory"""
        # Create prebuilt .mpy next to .py
        prebuilt_mpy = os.path.join(self.temp_dir, 'test.mpy')
        with open(prebuilt_mpy, 'wb') as f:
            f.write(b'MPY\x06\x03...')

        self.conn.feed(_pack_str('/test.mpy'))
        self.handler._do_stat()

        # Should return OK + stat for prebuilt .mpy
        result = bytes(self.conn._written)
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)
        size = struct.unpack('<I', result[5:9])[0]
        self.assertEqual(size, len(b'MPY\x06\x03...'))

    def test_stat_mpy_cache_found(self):
        """stat('test.mpy') finds cache when prebuilt doesn't exist"""
        cache_path = os.path.join(self.cache_dir, 'test.mpy-6.3.mpy')
        with open(cache_path, 'wb') as f:
            f.write(b'MPY\x06\x03cache...')
        # Use realpath for dict key (matches _resolve_path behavior)
        self.mpy_cross.compiled[os.path.realpath(self.test_py)] = cache_path

        self.conn.feed(_pack_str('/test.mpy'))
        self.handler._do_stat()

        # Should return OK + stat for cache .mpy
        result = bytes(self.conn._written)
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)
        size = struct.unpack('<I', result[5:9])[0]
        self.assertEqual(size, len(b'MPY\x06\x03cache...'))

    def test_stat_mpy_not_found(self):
        """stat('test.mpy') returns ENOENT when neither prebuilt nor cache"""
        self.conn.feed(_pack_str('/test.mpy'))
        self.handler._do_stat()

        # Should return ENOENT
        result = bytes(self.conn._written)
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, -errno.ENOENT)

    def test_open_mpy_prebuilt_priority(self):
        """open('test.mpy') prefers prebuilt over cache"""
        # Create both prebuilt and cache
        prebuilt_mpy = os.path.join(self.temp_dir, 'test.mpy')
        with open(prebuilt_mpy, 'wb') as f:
            f.write(b'PREBUILT')
        cache_path = os.path.join(self.cache_dir, 'test.mpy-6.3.mpy')
        with open(cache_path, 'wb') as f:
            f.write(b'CACHE')
        self.mpy_cross.compiled[os.path.realpath(self.test_py)] = cache_path

        self.conn.feed(_pack_str('/test.mpy'))
        self.conn.feed(_pack_str('rb'))
        self.handler._do_open()

        # Should return fd >= 0
        result = bytes(self.conn._written)
        fd = struct.unpack('b', result[0:1])[0]
        self.assertGreaterEqual(fd, 0)

        # Read should return prebuilt content
        self.conn._written.clear()
        self.conn.feed(struct.pack('b', fd))
        self.conn.feed(_pack_s32(1024))
        self.handler._do_read()

        result = bytes(self.conn._written)
        size = struct.unpack('<i', result[0:4])[0]
        data = result[4:4 + size]
        self.assertEqual(data, b'PREBUILT')

    def test_open_mpy_cache_fallback(self):
        """open('test.mpy') uses cache when prebuilt doesn't exist"""
        cache_path = os.path.join(self.cache_dir, 'test.mpy-6.3.mpy')
        with open(cache_path, 'wb') as f:
            f.write(b'CACHE')
        self.mpy_cross.compiled[os.path.realpath(self.test_py)] = cache_path

        self.conn.feed(_pack_str('/test.mpy'))
        self.conn.feed(_pack_str('rb'))
        self.handler._do_open()

        # Should return fd >= 0
        result = bytes(self.conn._written)
        fd = struct.unpack('b', result[0:1])[0]
        self.assertGreaterEqual(fd, 0)

        # Read should return cache content
        self.conn._written.clear()
        self.conn.feed(struct.pack('b', fd))
        self.conn.feed(_pack_s32(1024))
        self.handler._do_read()

        result = bytes(self.conn._written)
        size = struct.unpack('<i', result[0:4])[0]
        data = result[4:4 + size]
        self.assertEqual(data, b'CACHE')


class TestMountHandlerWrite(unittest.TestCase):
    """Tests for MountHandler write support (Phase 3)"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.conn = MockConnForHandler()

    def tearDown(self):
        import gc
        gc.collect()  # Force cleanup of handlers on Windows
        shutil.rmtree(self.temp_dir)

    def test_open_readonly_without_writable(self):
        """open() read-only file without writable flag works"""
        with open(os.path.join(self.temp_dir, 'test.txt'), 'w') as f:
            f.write('hello')
        handler = MountHandler(self.conn, self.temp_dir, writable=False)

        self.conn.feed(_pack_str('/test.txt') + _pack_str('rb'))
        handler.dispatch(CMD_OPEN)

        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertGreaterEqual(fd, 0)  # OK

    def test_open_write_without_writable(self):
        """open() write mode without writable flag returns EROFS"""
        handler = MountHandler(self.conn, self.temp_dir, writable=False)

        self.conn.feed(_pack_str('/test.txt') + _pack_str('wb'))
        handler.dispatch(CMD_OPEN)

        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertEqual(fd, -errno.EROFS)

    def test_open_append_without_writable(self):
        """open() append mode without writable flag returns EROFS"""
        handler = MountHandler(self.conn, self.temp_dir, writable=False)

        self.conn.feed(_pack_str('/test.txt') + _pack_str('ab'))
        handler.dispatch(CMD_OPEN)

        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertEqual(fd, -errno.EROFS)

    def test_open_readwrite_without_writable(self):
        """open() r+ mode without writable flag returns EROFS"""
        handler = MountHandler(self.conn, self.temp_dir, writable=False)

        self.conn.feed(_pack_str('/test.txt') + _pack_str('r+b'))
        handler.dispatch(CMD_OPEN)

        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertEqual(fd, -errno.EROFS)

    def test_open_write_with_writable(self):
        """open() write mode with writable flag works"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)

        self.conn.feed(_pack_str('/test.txt') + _pack_str('wb'))
        handler.dispatch(CMD_OPEN)

        data = self.conn.get_written()
        fd = struct.unpack('b', data[0:1])[0]
        self.assertGreaterEqual(fd, 0)  # OK

    def test_write_data(self):
        """write() writes data to fd"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)

        # Open for write
        self.conn.feed(_pack_str('/test.txt') + _pack_str('wb'))
        handler.dispatch(CMD_OPEN)
        fd = struct.unpack('b', self.conn.get_written()[0:1])[0]

        # Write data
        test_data = b'Hello World!\n'
        self.conn.feed(struct.pack('b', fd) + _pack_s32(len(test_data)) + test_data)
        handler.dispatch(CMD_WRITE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

        # Close
        handler.close_all()

        # Verify file content
        with open(os.path.join(self.temp_dir, 'test.txt'), 'rb') as f:
            content = f.read()
        self.assertEqual(content, test_data)

    def test_write_invalid_fd(self):
        """write() with invalid fd returns EBADF"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)

        self.conn.feed(struct.pack('b', 99) + _pack_s32(10) + b'0123456789')
        handler.dispatch(CMD_WRITE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, -errno.EBADF)

    def test_write_readonly_handler(self):
        """write() with readonly handler returns EROFS"""
        handler = MountHandler(self.conn, self.temp_dir, writable=False)

        self.conn.feed(struct.pack('b', 0) + _pack_s32(5) + b'hello')
        handler.dispatch(CMD_WRITE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, -errno.EROFS)

    def test_full_write_cycle(self):
        """Full cycle: open write, write, close, read back"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)

        # Open for write
        self.conn.feed(_pack_str('/data.bin') + _pack_str('wb'))
        handler.dispatch(CMD_OPEN)
        fd = struct.unpack('b', self.conn.get_written()[0:1])[0]

        # Write data
        test_data = b'Binary\x00Data\xff'
        self.conn.feed(struct.pack('b', fd) + _pack_s32(len(test_data)) + test_data)
        handler.dispatch(CMD_WRITE)
        self.conn.get_written()  # discard result

        # Close
        self.conn.feed(struct.pack('b', fd))
        handler.dispatch(CMD_CLOSE)

        # Open for read
        self.conn.feed(_pack_str('/data.bin') + _pack_str('rb'))
        handler.dispatch(CMD_OPEN)
        fd2 = struct.unpack('b', self.conn.get_written()[0:1])[0]

        # Read back
        self.conn.feed(struct.pack('b', fd2) + _pack_s32(1024))
        handler.dispatch(CMD_READ)

        result = self.conn.get_written()
        length = struct.unpack('<i', result[0:4])[0]
        content = result[4:4 + length]
        self.assertEqual(content, test_data)

    def test_mkdir_creates_directory(self):
        """mkdir() creates directory"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)

        self.conn.feed(_pack_str('/newdir'))
        handler.dispatch(CMD_MKDIR)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

        # Verify directory exists
        self.assertTrue(os.path.isdir(os.path.join(self.temp_dir, 'newdir')))

    def test_mkdir_exist_ok(self):
        """mkdir() on existing directory succeeds (exist_ok=True)"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)
        os.makedirs(os.path.join(self.temp_dir, 'existing'))

        self.conn.feed(_pack_str('/existing'))
        handler.dispatch(CMD_MKDIR)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

    def test_mkdir_creates_parents(self):
        """mkdir() creates parent directories"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)

        self.conn.feed(_pack_str('/a/b/c'))
        handler.dispatch(CMD_MKDIR)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

        # Verify all directories exist
        self.assertTrue(os.path.isdir(os.path.join(self.temp_dir, 'a', 'b', 'c')))

    def test_mkdir_readonly_handler(self):
        """mkdir() with readonly handler returns EROFS"""
        handler = MountHandler(self.conn, self.temp_dir, writable=False)

        self.conn.feed(_pack_str('/newdir'))
        handler.dispatch(CMD_MKDIR)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, -errno.EROFS)

    def test_remove_file(self):
        """remove() deletes file (recursive=0)"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)
        test_file = os.path.join(self.temp_dir, 'file.txt')
        with open(test_file, 'w') as f:
            f.write('test')

        self.conn.feed(_pack_str('/file.txt') + struct.pack('b', 0))
        handler.dispatch(CMD_REMOVE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

        # Verify file deleted
        self.assertFalse(os.path.exists(test_file))

    def test_remove_empty_dir(self):
        """remove() deletes empty directory (recursive=0)"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)
        test_dir = os.path.join(self.temp_dir, 'emptydir')
        os.makedirs(test_dir)

        self.conn.feed(_pack_str('/emptydir') + struct.pack('b', 0))
        handler.dispatch(CMD_REMOVE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

        # Verify dir deleted
        self.assertFalse(os.path.exists(test_dir))

    def test_remove_nonempty_dir_nonrecursive(self):
        """remove() on non-empty dir with recursive=0 fails"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)
        test_dir = os.path.join(self.temp_dir, 'nonempty')
        os.makedirs(test_dir)
        with open(os.path.join(test_dir, 'file.txt'), 'w') as f:
            f.write('content')

        self.conn.feed(_pack_str('/nonempty') + struct.pack('b', 0))
        handler.dispatch(CMD_REMOVE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertNotEqual(err, 0)  # Should fail

        # Verify dir still exists
        self.assertTrue(os.path.exists(test_dir))

    def test_remove_dir_recursive(self):
        """remove() deletes directory recursively (recursive=1)"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)
        test_dir = os.path.join(self.temp_dir, 'tree')
        os.makedirs(os.path.join(test_dir, 'sub'))
        with open(os.path.join(test_dir, 'file1.txt'), 'w') as f:
            f.write('a')
        with open(os.path.join(test_dir, 'sub', 'file2.txt'), 'w') as f:
            f.write('b')

        self.conn.feed(_pack_str('/tree') + struct.pack('b', 1))
        handler.dispatch(CMD_REMOVE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

        # Verify entire tree deleted
        self.assertFalse(os.path.exists(test_dir))

    def test_remove_file_recursive(self):
        """remove() on file with recursive=1 works"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)
        test_file = os.path.join(self.temp_dir, 'file.txt')
        with open(test_file, 'w') as f:
            f.write('test')

        self.conn.feed(_pack_str('/file.txt') + struct.pack('b', 1))
        handler.dispatch(CMD_REMOVE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, 0)  # OK

        # Verify file deleted
        self.assertFalse(os.path.exists(test_file))

    def test_remove_readonly_handler(self):
        """remove() with readonly handler returns EROFS"""
        handler = MountHandler(self.conn, self.temp_dir, writable=False)

        self.conn.feed(_pack_str('/file.txt') + struct.pack('b', 0))
        handler.dispatch(CMD_REMOVE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertEqual(err, -errno.EROFS)

    def test_remove_nonexistent(self):
        """remove() on nonexistent path returns error"""
        handler = MountHandler(self.conn, self.temp_dir, writable=True)

        self.conn.feed(_pack_str('/nonexistent') + struct.pack('b', 0))
        handler.dispatch(CMD_REMOVE)

        result = self.conn.get_written()
        err = struct.unpack('b', result[0:1])[0]
        self.assertNotEqual(err, 0)  # Should fail


if __name__ == '__main__':
    unittest.main()
