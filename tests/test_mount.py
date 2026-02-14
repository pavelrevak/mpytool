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
    CMD_MIN, CMD_MAX,
)


def _pack_s32(val):
    return struct.pack('<i', val)


def _pack_u32(val):
    return struct.pack('<I', val)


def _pack_str(s):
    data = s.encode('utf-8')
    return _pack_s32(len(data)) + data


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
        """listdir() returns all entries with modes"""
        self.conn.feed(_pack_str('/'))
        self.handler.dispatch(CMD_LISTDIR)
        data = self.conn.get_written()
        count = struct.unpack('<i', data[0:4])[0]
        self.assertEqual(count, 3)  # test.py, data.bin, lib/
        # Parse entries
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
        data = self.conn.get_written()
        count = struct.unpack('<i', data[0:4])[0]
        # Parse entry names
        names = []
        offset = 4
        for _ in range(count):
            name_len = struct.unpack('<i', data[offset:offset + 4])[0]
            offset += 4
            name = data[offset:offset + name_len].decode()
            offset += name_len
            offset += 4  # skip mode
            names.append(name)
        self.assertIn('test.py', names)
        self.assertIn('lib', names)  # virtual dir injected

    def test_listdir_submount(self):
        """listdir on submount path lists submount contents"""
        self.conn.feed(_pack_str('/lib'))
        self.handler.dispatch(CMD_LISTDIR)
        data = self.conn.get_written()
        count = struct.unpack('<i', data[0:4])[0]
        names = []
        offset = 4
        for _ in range(count):
            name_len = struct.unpack('<i', data[offset:offset + 4])[0]
            offset += 4
            name = data[offset:offset + name_len].decode()
            offset += name_len
            offset += 4
            names.append(name)
        self.assertIn('helper.py', names)
        self.assertIn('subdir', names)

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
        data = self.conn.get_written()
        count = struct.unpack('<i', data[0:4])[0]
        names = []
        offset = 4
        for _ in range(count):
            name_len = struct.unpack('<i', data[offset:offset + 4])[0]
            offset += 4
            name = data[offset:offset + name_len].decode()
            offset += name_len
            offset += 4
            names.append(name)
        # 'lib' should appear only once
        self.assertEqual(names.count('lib'), 1)


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
        # No output (VFS command consumed)
        self.assertIsNone(result)

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
        self.assertIsNone(result)
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
                d, '/remote', log=self.tool._log)

    def test_mount_custom_mountpoint(self):
        """mount with custom mount point"""
        with tempfile.TemporaryDirectory() as d:
            self.tool._dispatch_mount(
                [d, ':/app'], is_last_group=False)
            self.tool._mpy.mount.assert_called_once_with(
                d, '/app', log=self.tool._log)

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
        self.assertIsNone(result)

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


if __name__ == '__main__':
    unittest.main()
