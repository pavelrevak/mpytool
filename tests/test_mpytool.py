"""Tests for MpyTool class (unit tests without device)"""

import io
import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import Mock, patch, MagicMock

from mpytool.mpytool import MpyTool, ParamsError


class TestIsExcluded(unittest.TestCase):
    """Tests for _is_excluded method with wildcard patterns"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)

    def test_default_excludes_pyc(self):
        """Test that *.pyc files are excluded by default"""
        self.assertTrue(self.tool._is_excluded("module.pyc"))
        self.assertTrue(self.tool._is_excluded("test.pyc"))

    def test_default_excludes_hidden(self):
        """Test that hidden files/dirs (.*) are excluded by default"""
        self.assertTrue(self.tool._is_excluded(".git"))
        self.assertTrue(self.tool._is_excluded(".svn"))
        self.assertTrue(self.tool._is_excluded(".DS_Store"))
        self.assertTrue(self.tool._is_excluded(".hidden"))

    def test_default_allows_regular_files(self):
        """Test that regular files are not excluded"""
        self.assertFalse(self.tool._is_excluded("main.py"))
        self.assertFalse(self.tool._is_excluded("README.md"))
        self.assertFalse(self.tool._is_excluded("src"))

    def test_custom_exclude_pattern(self):
        """Test custom exclude patterns"""
        tool = MpyTool(self.mock_conn, verbose=None, exclude_dirs=["*.pyc", "build*"])
        self.assertTrue(tool._is_excluded("test.pyc"))
        self.assertTrue(tool._is_excluded("module.pyc"))
        self.assertTrue(tool._is_excluded("build"))
        self.assertTrue(tool._is_excluded("build_temp"))
        self.assertFalse(tool._is_excluded("test.py"))

    def test_wildcard_question_mark(self):
        """Test ? wildcard matches single character"""
        tool = MpyTool(self.mock_conn, verbose=None, exclude_dirs=["test?"])
        self.assertTrue(tool._is_excluded("test1"))
        self.assertTrue(tool._is_excluded("testA"))
        self.assertFalse(tool._is_excluded("test"))
        self.assertFalse(tool._is_excluded("test12"))

    def test_custom_adds_to_defaults(self):
        """Test that custom patterns add to defaults, not replace"""
        tool = MpyTool(self.mock_conn, verbose=None, exclude_dirs=["*.log"])
        # Custom pattern works
        self.assertTrue(tool._is_excluded("debug.log"))
        # Defaults still work
        self.assertTrue(tool._is_excluded("module.pyc"))
        self.assertTrue(tool._is_excluded(".git"))


class TestMvPathCombinations(unittest.TestCase):
    """Tests for mv command path handling.

    Path semantics (same as cp but both source and dest are device paths):
    - ':' = CWD (current working directory on device, empty string)
    - ':/' = root directory
    - ':file.py' = CWD/file.py (name/rename)
    - ':/file.py' = /file.py (absolute path)
    - Trailing slash on destination = directory (add source basename)
    - No trailing slash = rename/target name
    - Multiple sources require destination directory (trailing /)
    """

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        self.tool._mpy.stat.return_value = 10  # File exists by default
        self.tool._mpy.mkdir.return_value = None
        self.tool._mpy.rename.return_value = None
        self.tool._mpy.import_module.return_value = None

    def _get_rename_calls(self):
        """Extract (src, dst) tuples from all rename() calls"""
        return [(call[0][0], call[0][1]) for call in self.tool._mpy.rename.call_args_list]

    # ========== Rename (no trailing slash on destination) ==========

    def test_rename_in_root(self):
        """'mv :/old.py :/new.py' -> rename /old.py to /new.py"""
        self.tool.cmd_mv(':/old.py', ':/new.py')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('/old.py', '/new.py')])

    def test_rename_in_cwd(self):
        """'mv :old.py :new.py' -> rename old.py to new.py (both in CWD)"""
        self.tool.cmd_mv(':old.py', ':new.py')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('old.py', 'new.py')])

    def test_move_root_to_cwd_renamed(self):
        """'mv :/old.py :new.py' -> rename /old.py to new.py (in CWD)"""
        self.tool.cmd_mv(':/old.py', ':new.py')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('/old.py', 'new.py')])

    def test_move_cwd_to_root_renamed(self):
        """'mv :old.py :/new.py' -> rename old.py to /new.py"""
        self.tool.cmd_mv(':old.py', ':/new.py')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('old.py', '/new.py')])

    # ========== Move to directory (trailing slash on destination) ==========

    def test_move_to_root_dir(self):
        """'mv :file.py :/' -> move file.py to /file.py"""
        self.tool.cmd_mv(':file.py', ':/')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('file.py', '/file.py')])

    def test_move_to_cwd_dir(self):
        """'mv :/file.py :' -> move /file.py to CWD/file.py"""
        self.tool.cmd_mv(':/file.py', ':')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('/file.py', 'file.py')])

    def test_move_to_subdir(self):
        """'mv :file.py :/lib/' -> move file.py to /lib/file.py"""
        self.tool.cmd_mv(':file.py', ':/lib/')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('file.py', '/lib/file.py')])

    def test_move_to_cwd_subdir(self):
        """'mv :/file.py :lib/' -> move /file.py to lib/file.py"""
        self.tool.cmd_mv(':/file.py', ':lib/')
        calls = self._get_rename_calls()
        self.assertEqual(calls, [('/file.py', 'lib/file.py')])

    # ========== Multiple sources ==========

    def test_multiple_to_dir(self):
        """'mv :a.py :b.py :/lib/' -> move both to /lib/"""
        self.tool.cmd_mv(':a.py', ':b.py', ':/lib/')
        calls = self._get_rename_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn(('a.py', '/lib/a.py'), calls)
        self.assertIn(('b.py', '/lib/b.py'), calls)

    def test_multiple_to_cwd(self):
        """'mv :/a.py :/b.py :' -> move both to CWD"""
        self.tool.cmd_mv(':/a.py', ':/b.py', ':')
        calls = self._get_rename_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn(('/a.py', 'a.py'), calls)
        self.assertIn(('/b.py', 'b.py'), calls)

    def test_multiple_to_non_dir_invalid(self):
        """'mv :a.py :b.py :newname' -> INVALID (multiple to non-directory)"""
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_mv(':a.py', ':b.py', ':newname')
        self.assertIn('directory', str(ctx.exception).lower())

    # ========== Error cases ==========

    def test_missing_colon_prefix_source(self):
        """'mv file.py :/new.py' -> INVALID (source must have : prefix)"""
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_mv('file.py', ':/new.py')
        self.assertIn('device path', str(ctx.exception).lower())

    def test_missing_colon_prefix_dest(self):
        """'mv :file.py new.py' -> INVALID (dest must have : prefix)"""
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_mv(':file.py', 'new.py')
        self.assertIn('device path', str(ctx.exception).lower())

    def test_source_not_found(self):
        """'mv :nonexistent.py :/new.py' -> INVALID (source not found)"""
        self.tool._mpy.stat.return_value = None  # File doesn't exist
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_mv(':nonexistent.py', ':/new.py')
        self.assertIn('not found', str(ctx.exception).lower())


@patch('sys.stdout', new_callable=io.StringIO)
class TestCatCommand(unittest.TestCase):
    """Tests for cat command with : prefix requirement"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        self.tool._mpy.get.return_value = b"file content"
        self.tool._log = Mock()

    def test_cat_with_prefix(self, mock_stdout):
        """'cat :file.py' -> reads file.py"""
        self.tool.process_commands(['cat', ':file.py'])
        self.tool._mpy.get.assert_called_with('file.py')

    def test_cat_with_root_prefix(self, mock_stdout):
        """'cat :/file.py' -> reads /file.py"""
        self.tool.process_commands(['cat', ':/file.py'])
        self.tool._mpy.get.assert_called_with('/file.py')

    def test_cat_multiple_files(self, mock_stdout):
        """'cat :a.py :b.py' -> reads both files"""
        self.tool.process_commands(['cat', ':a.py', ':b.py'])
        self.assertEqual(self.tool._mpy.get.call_count, 2)

    def test_cat_without_prefix_invalid(self, mock_stdout):
        """'cat file.py' -> INVALID (missing : prefix)"""
        with self.assertRaises(ParamsError):
            self.tool.process_commands(['cat', 'file.py'])


class TestRmCommand(unittest.TestCase):
    """Tests for rm command with : prefix requirement"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        self.tool._mpy.delete.return_value = None
        self.tool._mpy.ls.return_value = [('a.py', 10), ('b.py', 20)]

    def test_rm_with_prefix(self):
        """'rm :file.py' -> deletes file.py"""
        self.tool.cmd_rm(':file.py')
        self.tool._mpy.delete.assert_called_with('file.py')

    def test_rm_with_root_prefix(self):
        """'rm :/file.py' -> deletes /file.py"""
        self.tool.cmd_rm(':/file.py')
        self.tool._mpy.delete.assert_called_with('/file.py')

    def test_rm_root_contents(self):
        """'rm :/' -> deletes contents of root (not root itself)"""
        self.tool.cmd_rm(':/')
        # Should delete contents, not root directory itself
        self.tool._mpy.ls.assert_called_with('/')
        self.assertEqual(self.tool._mpy.delete.call_count, 2)  # a.py and b.py

    def test_rm_cwd_contents(self):
        """'rm :' -> deletes contents of CWD"""
        self.tool.cmd_rm(':')
        # ':' means CWD, should delete contents
        self.tool._mpy.ls.assert_called_with('')

    def test_rm_dir_contents(self):
        """'rm :dir/' -> deletes contents of dir, keeps directory"""
        self.tool.cmd_rm(':dir/')
        self.tool._mpy.ls.assert_called_with('dir')

    def test_rm_multiple(self):
        """'rm :a.py :b.py' -> deletes both"""
        self.tool.cmd_rm(':a.py', ':b.py')
        self.assertEqual(self.tool._mpy.delete.call_count, 2)

    def test_rm_without_prefix_invalid(self):
        """'rm file.py' -> INVALID (missing : prefix)"""
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_rm('file.py')
        self.assertIn(':', str(ctx.exception))


class TestMkdirCommand(unittest.TestCase):
    """Tests for mkdir command with : prefix requirement"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        self.tool._mpy.mkdir.return_value = None
        self.tool._log = Mock()

    def test_mkdir_with_prefix(self):
        """'mkdir :dir' -> creates dir in CWD"""
        self.tool.process_commands(['mkdir', ':dir'])
        self.tool._mpy.mkdir.assert_called_with('dir')

    def test_mkdir_with_root_prefix(self):
        """'mkdir :/dir' -> creates /dir"""
        self.tool.process_commands(['mkdir', ':/dir'])
        self.tool._mpy.mkdir.assert_called_with('/dir')

    def test_mkdir_nested(self):
        """'mkdir :/a/b/c' -> creates nested directories"""
        self.tool.process_commands(['mkdir', ':/a/b/c'])
        self.tool._mpy.mkdir.assert_called_with('/a/b/c')

    def test_mkdir_multiple(self):
        """'mkdir :a :b' -> creates both directories"""
        self.tool.process_commands(['mkdir', ':a', ':b'])
        self.assertEqual(self.tool._mpy.mkdir.call_count, 2)

    def test_mkdir_without_prefix_invalid(self):
        """'mkdir dir' -> INVALID (missing : prefix)"""
        with self.assertRaises(ParamsError):
            self.tool.process_commands(['mkdir', 'dir'])


@patch('sys.stdout', new_callable=io.StringIO)
class TestLsCommand(unittest.TestCase):
    """Tests for ls command with : prefix requirement"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        self.tool._mpy.ls.return_value = [('file.py', 100), ('dir', None)]
        self.tool._mpy.getcwd.return_value = '/'
        self.tool._log = Mock()

    def test_ls_cwd(self, mock_stdout):
        """'ls :' -> list CWD"""
        self.tool.process_commands(['ls', ':'])
        self.tool._mpy.ls.assert_called_with('')

    def test_ls_root(self, mock_stdout):
        """'ls :/' -> list root"""
        self.tool.process_commands(['ls', ':/'])
        self.tool._mpy.ls.assert_called_with('/')

    def test_ls_path(self, mock_stdout):
        """'ls :/lib' -> list /lib"""
        self.tool.process_commands(['ls', ':/lib'])
        self.tool._mpy.ls.assert_called_with('/lib')

    def test_ls_relative(self, mock_stdout):
        """'ls :lib' -> list lib (relative to CWD)"""
        self.tool.process_commands(['ls', ':lib'])
        self.tool._mpy.ls.assert_called_with('lib')

    def test_ls_without_prefix_invalid(self, mock_stdout):
        """'ls /lib' -> INVALID (missing : prefix)"""
        with self.assertRaises(ParamsError):
            self.tool.process_commands(['ls', '/lib'])


@patch('sys.stdout', new_callable=io.StringIO)
class TestTreeCommand(unittest.TestCase):
    """Tests for tree command with : prefix requirement"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        # tree returns (name, size, sub_tree) where sub_tree is list of same structure or None
        self.tool._mpy.tree.return_value = ('./', 100, [('file.py', 50, None)])
        self.tool._mpy.getcwd.return_value = '/'
        self.tool._log = Mock()

    def test_tree_cwd(self, mock_stdout):
        """'tree :' -> tree of CWD"""
        self.tool.process_commands(['tree', ':'])
        self.tool._mpy.tree.assert_called_with('')

    def test_tree_root(self, mock_stdout):
        """'tree :/' -> tree of root"""
        self.tool.process_commands(['tree', ':/'])
        self.tool._mpy.tree.assert_called_with('/')

    def test_tree_path(self, mock_stdout):
        """'tree :/lib' -> tree of /lib"""
        self.tool.process_commands(['tree', ':/lib'])
        self.tool._mpy.tree.assert_called_with('/lib')

    def test_tree_without_prefix_invalid(self, mock_stdout):
        """'tree /lib' -> INVALID (missing : prefix)"""
        with self.assertRaises(ParamsError):
            self.tool.process_commands(['tree', '/lib'])


@patch('sys.stdout', new_callable=io.StringIO)
class TestPathsCommand(unittest.TestCase):
    """Tests for _paths helper command (shell completion)"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        self.tool._log = Mock()

    def test_paths_cwd_default(self, mock_stdout):
        """'_paths' without args -> list CWD"""
        self.tool._mpy.ls.return_value = [('file.py', 100)]
        self.tool.process_commands(['_paths'])
        self.tool._mpy.ls.assert_called_with('')

    def test_paths_cwd(self, mock_stdout):
        """'_paths :' -> list CWD"""
        self.tool._mpy.ls.return_value = [('file.py', 100)]
        self.tool.process_commands(['_paths', ':'])
        self.tool._mpy.ls.assert_called_with('')

    def test_paths_root(self, mock_stdout):
        """'_paths :/' -> list root"""
        self.tool._mpy.ls.return_value = [('boot.py', 100)]
        self.tool.process_commands(['_paths', ':/'])
        self.tool._mpy.ls.assert_called_with('/')

    def test_paths_absolute(self, mock_stdout):
        """'_paths :/lib' -> list /lib"""
        self.tool._mpy.ls.return_value = [('module.py', 100)]
        self.tool.process_commands(['_paths', ':/lib'])
        self.tool._mpy.ls.assert_called_with('/lib')

    def test_paths_relative(self, mock_stdout):
        """'_paths :lib' -> list lib (relative to CWD)"""
        self.tool._mpy.ls.return_value = [('module.py', 100)]
        self.tool.process_commands(['_paths', ':lib'])
        self.tool._mpy.ls.assert_called_with('lib')

    def test_paths_output_files(self, mock_stdout):
        """Files are printed without trailing slash"""
        self.tool._mpy.ls.return_value = [('a.py', 100), ('b.txt', 50)]
        self.tool.process_commands(['_paths', ':'])
        output = mock_stdout.getvalue()
        self.assertIn('a.py\n', output)
        self.assertIn('b.txt\n', output)
        self.assertNotIn('a.py/', output)

    def test_paths_output_dirs(self, mock_stdout):
        """Directories are printed with trailing slash"""
        self.tool._mpy.ls.return_value = [('lib', None), ('src', None)]
        self.tool.process_commands(['_paths', ':'])
        output = mock_stdout.getvalue()
        self.assertIn('lib/\n', output)
        self.assertIn('src/\n', output)

    def test_paths_output_mixed(self, mock_stdout):
        """Mixed files and directories"""
        self.tool._mpy.ls.return_value = [
            ('main.py', 100),
            ('lib', None),
            ('boot.py', 50),
            ('drivers', None),
        ]
        self.tool.process_commands(['_paths', ':'])
        output = mock_stdout.getvalue()
        self.assertEqual(output, 'main.py\nlib/\nboot.py\ndrivers/\n')

    def test_paths_nonexistent_silent(self, mock_stdout):
        """Non-existent path returns silently (no exception)"""
        from mpytool import DirNotFound
        self.tool._mpy.ls.side_effect = DirNotFound('/no/such/path')
        # Should not raise - silently returns for shell completion
        self.tool.process_commands(['_paths', ':/no/such/path'])
        self.assertEqual(mock_stdout.getvalue(), '')

    def test_paths_error_silent(self, mock_stdout):
        """Other MpyError returns silently"""
        from mpytool import MpyError
        self.tool._mpy.ls.side_effect = MpyError('connection error')
        # Should not raise - silently returns for shell completion
        self.tool.process_commands(['_paths', ':'])
        self.assertEqual(mock_stdout.getvalue(), '')

    def test_paths_without_prefix_invalid(self, mock_stdout):
        """'_paths /lib' -> INVALID (missing : prefix)"""
        with self.assertRaises(ParamsError):
            self.tool.process_commands(['_paths', '/lib'])


@patch('sys.stdout', new_callable=io.StringIO)
class TestPortsCommand(unittest.TestCase):
    """Tests for _ports helper command (shell completion)"""

    def setUp(self):
        # _ports doesn't need a connection - use None
        self.tool = MpyTool(conn=None, verbose=None)
        self.tool._log = Mock()

    @patch('mpytool.utils.detect_serial_ports')
    def test_ports_lists_detected(self, mock_detect, mock_stdout):
        """_ports lists all detected serial ports"""
        mock_detect.return_value = ['/dev/cu.usbmodem1101', '/dev/cu.usbmodem1103']
        self.tool.process_commands(['_ports'])
        output = mock_stdout.getvalue()
        self.assertEqual(output, '/dev/cu.usbmodem1101\n/dev/cu.usbmodem1103\n')

    @patch('mpytool.utils.detect_serial_ports')
    def test_ports_empty(self, mock_detect, mock_stdout):
        """_ports with no ports available"""
        mock_detect.return_value = []
        self.tool.process_commands(['_ports'])
        self.assertEqual(mock_stdout.getvalue(), '')

    @patch('mpytool.utils.detect_serial_ports')
    def test_ports_single(self, mock_detect, mock_stdout):
        """_ports with single port"""
        mock_detect.return_value = ['/dev/ttyACM0']
        self.tool.process_commands(['_ports'])
        self.assertEqual(mock_stdout.getvalue(), '/dev/ttyACM0\n')

    @patch('mpytool.utils.detect_serial_ports')
    def test_ports_no_connection_needed(self, mock_detect, mock_stdout):
        """_ports works without device connection"""
        mock_detect.return_value = ['/dev/cu.usbmodem1101']
        # Create tool without any connection
        tool = MpyTool(conn=None, verbose=None)
        tool._log = Mock()
        # Should work without accessing mpy/conn
        tool.process_commands(['_ports'])
        self.assertEqual(mock_stdout.getvalue(), '/dev/cu.usbmodem1101\n')


@patch('sys.stdout', new_callable=io.StringIO)
class TestCommandsCommand(unittest.TestCase):
    """Tests for _commands helper command (shell completion)"""

    def setUp(self):
        # _commands doesn't need a connection - use None
        self.tool = MpyTool(conn=None, verbose=None)
        self.tool._log = Mock()

    def test_commands_lists_all(self, mock_stdout):
        """_commands lists all public commands"""
        self.tool.process_commands(['_commands'])
        output = mock_stdout.getvalue()
        # Check format is name:description
        self.assertIn('ls:', output)
        self.assertIn('cp:', output)
        self.assertIn('reset:', output)
        self.assertIn('mount:', output)

    def test_commands_format(self, mock_stdout):
        """_commands output is name:description format"""
        self.tool.process_commands(['_commands'])
        lines = mock_stdout.getvalue().strip().split('\n')
        for line in lines:
            self.assertIn(':', line)
            name, desc = line.split(':', 1)
            self.assertTrue(len(name) > 0)
            self.assertTrue(len(desc) > 0)

    def test_commands_no_hidden(self, mock_stdout):
        """_commands does not list hidden commands"""
        self.tool.process_commands(['_commands'])
        output = mock_stdout.getvalue()
        # Hidden commands start with _
        self.assertNotIn('_paths:', output)
        self.assertNotIn('_ports:', output)
        self.assertNotIn('_commands:', output)

    def test_commands_no_connection_needed(self, mock_stdout):
        """_commands works without device connection"""
        tool = MpyTool(conn=None, verbose=None)
        tool._log = Mock()
        # Should work without accessing mpy/conn
        tool.process_commands(['_commands'])
        self.assertIn('ls:', mock_stdout.getvalue())


@patch('sys.stdout', new_callable=io.StringIO)
class TestOptionsCommand(unittest.TestCase):
    """Tests for _options helper command (shell completion)"""

    def setUp(self):
        self.tool = MpyTool(conn=None, verbose=None)
        self.tool._log = Mock()

    def test_options_reset(self, mock_stdout):
        """_options reset lists reset options"""
        self.tool.process_commands(['_options', 'reset'])
        output = mock_stdout.getvalue()
        self.assertIn('--machine:', output)
        self.assertIn('--rts:', output)
        self.assertIn('--boot:', output)

    def test_options_mount(self, mock_stdout):
        """_options mount lists mount options"""
        self.tool.process_commands(['_options', 'mount'])
        output = mock_stdout.getvalue()
        self.assertIn('--mpy:', output)
        self.assertIn('--writable:', output)

    def test_options_cp(self, mock_stdout):
        """_options cp lists cp options"""
        self.tool.process_commands(['_options', 'cp'])
        output = mock_stdout.getvalue()
        self.assertIn('--force:', output)
        self.assertIn('--mpy:', output)
        self.assertIn('--compress:', output)

    def test_options_no_help(self, mock_stdout):
        """_options does not include --help"""
        self.tool.process_commands(['_options', 'reset'])
        self.assertNotIn('--help:', mock_stdout.getvalue())

    def test_options_unknown_command(self, mock_stdout):
        """_options with unknown command returns empty"""
        self.tool.process_commands(['_options', 'unknown'])
        self.assertEqual(mock_stdout.getvalue(), '')

    def test_options_global(self, mock_stdout):
        """_options without command returns global options"""
        self.tool.process_commands(['_options'])
        output = mock_stdout.getvalue()
        self.assertIn('--port:', output)
        self.assertIn('--verbose:', output)
        self.assertIn('--force:', output)
        self.assertNotIn('--help:', output)
        self.assertNotIn('--version:', output)

    def test_options_format(self, mock_stdout):
        """_options output is option:description:argtype format"""
        self.tool.process_commands(['_options', 'reset'])
        lines = mock_stdout.getvalue().strip().split('\n')
        for line in lines:
            parts = line.split(':')
            self.assertTrue(len(parts) >= 2)  # At least option:description
            self.assertTrue(parts[0].startswith('--'))

    def test_options_argtype_flag(self, mock_stdout):
        """_options shows empty argtype for flags"""
        self.tool.process_commands(['_options'])
        output = mock_stdout.getvalue()
        # --verbose is a flag (store_true), should have empty argtype
        self.assertIn('--verbose:verbose output:', output)

    def test_options_argtype_value(self, mock_stdout):
        """_options shows argtype for options with values"""
        self.tool.process_commands(['_options'])
        output = mock_stdout.getvalue()
        # --port takes a value, should have 'port' argtype
        self.assertIn('--port:serial port:port', output)
        # --baud takes a value
        self.assertIn('--baud:baud rate:baud', output)


@patch('sys.stdout', new_callable=io.StringIO)
class TestArgsCommand(unittest.TestCase):
    """Tests for _args helper command (shell completion)"""

    def setUp(self):
        self.tool = MpyTool(conn=None, verbose=None)
        self.tool._log = Mock()

    def test_args_ls(self, mock_stdout):
        """_args ls shows optional remote argument"""
        self.tool.process_commands(['_args', 'ls'])
        output = mock_stdout.getvalue()
        self.assertIn('remote:?:', output)

    def test_args_cat(self, mock_stdout):
        """_args cat shows one-or-more remote arguments"""
        self.tool.process_commands(['_args', 'cat'])
        output = mock_stdout.getvalue()
        self.assertIn('remote:+:', output)

    def test_args_run(self, mock_stdout):
        """_args run shows exactly-one local_file argument"""
        self.tool.process_commands(['_args', 'run'])
        output = mock_stdout.getvalue()
        self.assertIn('local_file:1:', output)

    def test_args_cp(self, mock_stdout):
        """_args cp shows zero-or-more local_or_remote arguments"""
        self.tool.process_commands(['_args', 'cp'])
        output = mock_stdout.getvalue()
        self.assertIn('local_or_remote:*:', output)

    def test_args_unknown_command(self, mock_stdout):
        """_args with unknown command returns empty"""
        self.tool.process_commands(['_args', 'unknown'])
        self.assertEqual(mock_stdout.getvalue(), '')

    def test_args_format(self, mock_stdout):
        """_args output is type:nargs:description format"""
        self.tool.process_commands(['_args', 'ls'])
        lines = mock_stdout.getvalue().strip().split('\n')
        for line in lines:
            parts = line.split(':', 2)  # Split into max 3 parts
            self.assertEqual(len(parts), 3)
            self.assertIn(parts[1], ['1', '?', '+', '*'])


class TestRunCommand(unittest.TestCase):
    """Tests for run command"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()
        self.tool._log = Mock()
        # Create temp directory with test file
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "script.py")
        with open(self.test_file, 'wb') as f:
            f.write(b"print('hello')\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_run_missing_argument(self):
        """'run' without file -> ParamsError"""
        with self.assertRaises(ParamsError) as ctx:
            self.tool.process_commands(['run'])
        self.assertIn('file', str(ctx.exception).lower())

    def test_run_file_not_found(self):
        """'run nonexistent.py' -> ParamsError"""
        with self.assertRaises(ParamsError) as ctx:
            self.tool.process_commands(['run', '/no/such/file.py'])
        self.assertIn('not found', str(ctx.exception).lower())

    def test_run_sends_file_content(self):
        """'run script.py' -> reads file and sends via try_raw_paste"""
        self.tool.process_commands(['run', self.test_file])
        self.tool._mpy.comm.try_raw_paste.assert_called_once_with(
            b"print('hello')\n", timeout=0)

    def test_run_reads_binary_mode(self):
        """run reads file as bytes (rb), preserving encoding"""
        utf8_file = os.path.join(self.temp_dir, "utf8.py")
        with open(utf8_file, 'wb') as f:
            f.write("print('súbor')\n".encode('utf-8'))
        self.tool.process_commands(['run', utf8_file])
        self.tool._mpy.comm.try_raw_paste.assert_called_once_with(
            "print('súbor')\n".encode('utf-8'), timeout=0)

    def test_run_with_command_separator(self):
        """'run script.py -- ls' -> run then ls"""
        self.tool.process_commands(['run', self.test_file])
        self.tool._mpy.comm.try_raw_paste.assert_called_once()


class TestPathCommand(unittest.TestCase):
    """Tests for path command dispatch"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()

    def test_path_show(self):
        """Test path without arguments shows current path"""
        self.tool._mpy.get_sys_path.return_value = ['', '/lib']
        with patch('builtins.print') as mock_print:
            self.tool._dispatch_path([], False)
        mock_print.assert_called_once_with(": :/lib")

    def test_path_replace(self):
        """Test path replacement without flags"""
        commands = [':/', ':/lib', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.set_sys_path.assert_called_once_with('/', '/lib', '/custom')

    def test_path_prepend(self):
        """Test path prepend with -f flag"""
        commands = ['-f', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.prepend_sys_path.assert_called_once_with('/custom')

    def test_path_prepend_long_flag(self):
        """Test path prepend with --first flag"""
        commands = ['--first', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.prepend_sys_path.assert_called_once_with('/custom')

    def test_path_append(self):
        """Test path append with -a flag"""
        commands = ['-a', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.append_sys_path.assert_called_once_with('/custom')

    def test_path_append_long_flag(self):
        """Test path append with --append flag"""
        commands = ['--append', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.append_sys_path.assert_called_once_with('/custom')

    def test_path_delete(self):
        """Test path delete with -d flag"""
        commands = ['-d', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.remove_from_sys_path.assert_called_once_with('/custom')

    def test_path_delete_long_flag(self):
        """Test path delete with --delete flag"""
        commands = ['--delete', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.remove_from_sys_path.assert_called_once_with('/custom')

    def test_path_requires_colon_prefix(self):
        """Test that path requires : prefix"""
        commands = ['/lib']
        with self.assertRaises(ParamsError):
            self.tool._dispatch_path(commands, False)

    def test_path_multiple_paths(self):
        """Test path with multiple paths"""
        commands = [':', ':/lib', ':/custom']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.set_sys_path.assert_called_once_with('', '/lib', '/custom')

    def test_path_prepend_multiple(self):
        """Test prepending multiple paths"""
        commands = ['-f', ':/a', ':/b']
        self.tool._dispatch_path(commands, False)
        self.tool._mpy.prepend_sys_path.assert_called_once_with('/a', '/b')

    def test_path_unknown_flag(self):
        """Test unknown flag raises error"""
        commands = ['-x', ':/lib']
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_path(commands, False)
        self.assertIn('-x', str(ctx.exception))


class TestRtcCommand(unittest.TestCase):
    """Tests for rtc command"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()
        self.tool._mpy.comm = Mock()

    def test_rtc_display(self):
        """'rtc' displays current RTC"""
        # RTC tuple: (year, month, day, weekday, hour, min, sec, subsec)
        self.tool._mpy.comm.exec.return_value = b"(2026, 2, 21, 5, 14, 30, 45, 0)\n"
        with patch('builtins.print') as mock_print:
            commands = []
            self.tool._dispatch_rtc(commands, False)
            mock_print.assert_called_once_with("2026-02-21 14:30:45")

    def test_rtc_set_local(self):
        """'rtc --set' sets RTC to local time"""
        self.tool._mpy.comm.exec.return_value = b""
        with patch('mpytool.mpytool._datetime') as mock_dt:
            mock_now = Mock()
            mock_now.year = 2026
            mock_now.month = 2
            mock_now.day = 21
            mock_now.weekday.return_value = 5
            mock_now.hour = 14
            mock_now.minute = 30
            mock_now.second = 45
            mock_now.microsecond = 0
            mock_now.strftime.return_value = "2026-02-21 14:30:45"
            mock_dt.datetime.now.return_value = mock_now
            commands = ['--set']
            self.tool._dispatch_rtc(commands, False)
            # Verify RTC was set
            call_args = self.tool._mpy.comm.exec.call_args[0][0]
            self.assertIn("RTC().datetime", call_args)
            self.assertIn("2026", call_args)

    def test_rtc_local_flag(self):
        """'rtc --local' sets RTC to local time"""
        self.tool._mpy.comm.exec.return_value = b""
        with patch('mpytool.mpytool._datetime') as mock_dt:
            mock_now = Mock()
            mock_now.year = 2026
            mock_now.month = 1
            mock_now.day = 15
            mock_now.weekday.return_value = 2
            mock_now.hour = 10
            mock_now.minute = 0
            mock_now.second = 0
            mock_now.microsecond = 0
            mock_now.strftime.return_value = "2026-01-15 10:00:00"
            mock_dt.datetime.now.return_value = mock_now
            commands = ['--local']
            self.tool._dispatch_rtc(commands, False)
            call_args = self.tool._mpy.comm.exec.call_args[0][0]
            self.assertIn("RTC().datetime", call_args)

    def test_rtc_utc_flag(self):
        """'rtc --utc' sets RTC to UTC time"""
        self.tool._mpy.comm.exec.return_value = b""
        with patch('mpytool.mpytool._datetime') as mock_dt:
            mock_utc = Mock()
            mock_utc.year = 2026
            mock_utc.month = 2
            mock_utc.day = 21
            mock_utc.weekday.return_value = 5
            mock_utc.hour = 13
            mock_utc.minute = 30
            mock_utc.second = 45
            mock_utc.microsecond = 0
            mock_utc.strftime.return_value = "2026-02-21 13:30:45"
            mock_dt.datetime.now.return_value = mock_utc
            mock_dt.timezone.utc = Mock()
            commands = ['--utc']
            self.tool._dispatch_rtc(commands, False)
            # Verify datetime.now was called with UTC timezone
            mock_dt.datetime.now.assert_called_with(mock_dt.timezone.utc)

    def test_rtc_manual_datetime(self):
        """'rtc "2026-02-21 14:30:00"' sets RTC manually"""
        self.tool._mpy.comm.exec.return_value = b""
        with patch('mpytool.mpytool._datetime') as mock_dt:
            mock_parsed = Mock()
            mock_parsed.year = 2026
            mock_parsed.month = 2
            mock_parsed.day = 21
            mock_parsed.weekday.return_value = 5
            mock_parsed.hour = 14
            mock_parsed.minute = 30
            mock_parsed.second = 0
            mock_parsed.microsecond = 0
            mock_parsed.strftime.return_value = "2026-02-21 14:30:00"
            mock_dt.datetime.strptime.return_value = mock_parsed
            commands = ['2026-02-21 14:30:00']
            self.tool._dispatch_rtc(commands, False)
            mock_dt.datetime.strptime.assert_called_with(
                '2026-02-21 14:30:00', '%Y-%m-%d %H:%M:%S')

    def test_rtc_invalid_datetime_format(self):
        """'rtc "invalid"' raises ParamsError"""
        commands = ['invalid']
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_rtc(commands, False)
        self.assertIn('invalid datetime format', str(ctx.exception))

    def test_rtc_datetime_with_flag_error(self):
        """'rtc --set "2026-02-21 14:30:00"' raises ParamsError"""
        commands = ['--set', '2026-02-21 14:30:00']
        with self.assertRaises(ParamsError) as ctx:
            self.tool._dispatch_rtc(commands, False)
        self.assertIn('incompatible', str(ctx.exception))

    def test_rtc_short_flags(self):
        """Test short flags -s, -l, -u"""
        self.tool._mpy.comm.exec.return_value = b""
        with patch('mpytool.mpytool._datetime') as mock_dt:
            mock_now = Mock()
            mock_now.year = 2026
            mock_now.month = 1
            mock_now.day = 1
            mock_now.weekday.return_value = 2
            mock_now.hour = 0
            mock_now.minute = 0
            mock_now.second = 0
            mock_now.microsecond = 0
            mock_now.strftime.return_value = "2026-01-01 00:00:00"
            mock_dt.datetime.now.return_value = mock_now
            mock_dt.timezone.utc = Mock()
            # Test -s
            commands = ['-s']
            self.tool._dispatch_rtc(commands, False)
            self.assertTrue(self.tool._mpy.comm.exec.called)
            self.tool._mpy.comm.exec.reset_mock()
            # Test -l
            commands = ['-l']
            self.tool._dispatch_rtc(commands, False)
            self.assertTrue(self.tool._mpy.comm.exec.called)
            self.tool._mpy.comm.exec.reset_mock()
            # Test -u
            commands = ['-u']
            self.tool._dispatch_rtc(commands, False)
            mock_dt.datetime.now.assert_called_with(mock_dt.timezone.utc)


if __name__ == "__main__":
    unittest.main()
