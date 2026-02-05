"""Tests for MpyTool class (unit tests without device)"""

import io
import os
import sys
import tempfile
import shutil
import hashlib
import unittest
from unittest.mock import Mock, patch, MagicMock

from mpytool.mpytool import MpyTool, ParamsError


class TestCollectDstFiles(unittest.TestCase):
    """Tests for _collect_dst_files method"""

    def setUp(self):
        # Create a mock connection and MpyTool instance
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        # Create temp directory structure for tests
        self.temp_dir = tempfile.mkdtemp()
        # Create test files
        self.test_file = os.path.join(self.temp_dir, "file.txt")
        with open(self.test_file, "w") as f:
            f.write("test content")  # 12 bytes
        # Create test directory with files
        self.test_subdir = os.path.join(self.temp_dir, "subdir")
        os.makedirs(self.test_subdir)
        self.test_subfile1 = os.path.join(self.test_subdir, "a.txt")
        self.test_subfile2 = os.path.join(self.test_subdir, "b.txt")
        with open(self.test_subfile1, "w") as f:
            f.write("a")  # 1 byte
        with open(self.test_subfile2, "w") as f:
            f.write("bb")  # 2 bytes
        # Create nested directory
        self.test_nested = os.path.join(self.test_subdir, "nested")
        os.makedirs(self.test_nested)
        self.test_nested_file = os.path.join(self.test_nested, "c.txt")
        with open(self.test_nested_file, "w") as f:
            f.write("ccc")  # 3 bytes

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_single_file_to_root(self):
        """Test collecting path and size for single file to root"""
        files = self.tool._collect_dst_files(self.test_file, "/")
        self.assertEqual(files, {"/file.txt": 12})

    def test_single_file_to_dir(self):
        """Test collecting path and size for single file to directory"""
        files = self.tool._collect_dst_files(self.test_file, "/dest/")
        self.assertEqual(files, {"/dest/file.txt": 12})

    def test_single_file_with_explicit_name(self):
        """Test collecting path and size for single file with explicit destination name"""
        files = self.tool._collect_dst_files(self.test_file, "/dest/renamed.txt")
        self.assertEqual(files, {"/dest/renamed.txt": 12})

    def test_directory_with_basename(self):
        """Test collecting paths and sizes for directory (adds src basename)"""
        files = self.tool._collect_dst_files(self.test_subdir, "/", add_src_basename=True)
        self.assertEqual(files["/subdir/a.txt"], 1)
        self.assertEqual(files["/subdir/b.txt"], 2)
        self.assertEqual(files["/subdir/nested/c.txt"], 3)
        self.assertEqual(len(files), 3)

    def test_directory_without_basename(self):
        """Test collecting paths and sizes for directory contents (no src basename)"""
        files = self.tool._collect_dst_files(self.test_subdir, "/dest", add_src_basename=False)
        self.assertEqual(files["/dest/a.txt"], 1)
        self.assertEqual(files["/dest/b.txt"], 2)
        self.assertEqual(files["/dest/nested/c.txt"], 3)
        self.assertEqual(len(files), 3)

    def test_excludes_pyc_files(self):
        """Test that *.pyc files are excluded"""
        pyc_file = os.path.join(self.test_subdir, "module.pyc")
        with open(pyc_file, "w") as f:
            f.write("bytecode")
        files = self.tool._collect_dst_files(self.test_subdir, "/", add_src_basename=True)
        self.assertNotIn("/subdir/module.pyc", files)
        self.assertEqual(len(files), 3)  # only a.txt, b.txt, nested/c.txt

    def test_nonexistent_path(self):
        """Test that nonexistent path returns empty dict"""
        files = self.tool._collect_dst_files("/nonexistent/path", "/")
        self.assertEqual(files, {})


class TestFileNeedsUpdateWithCache(unittest.TestCase):
    """Tests for _file_needs_update with cache"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()  # Replace with mock
        self.test_data = b"test content"
        self.test_hash = hashlib.sha256(self.test_data).digest()

    def test_uses_cache_when_available(self):
        """Test that cache is used instead of device calls"""
        # Pre-populate cache
        self.tool._remote_file_cache["/test.txt"] = (len(self.test_data), self.test_hash)
        # Should return False (file unchanged) without any device calls
        result = self.tool._file_needs_update(self.test_data, "/test.txt")
        self.assertFalse(result)
        # Verify no device calls were made
        self.tool._mpy.stat.assert_not_called()
        self.tool._mpy.hashfile.assert_not_called()

    def test_cache_none_means_needs_update(self):
        """Test that None in cache means file doesn't exist"""
        self.tool._remote_file_cache["/missing.txt"] = None
        result = self.tool._file_needs_update(self.test_data, "/missing.txt")
        self.assertTrue(result)

    def test_cache_size_mismatch(self):
        """Test that size mismatch in cache returns True"""
        self.tool._remote_file_cache["/test.txt"] = (999, self.test_hash)
        result = self.tool._file_needs_update(self.test_data, "/test.txt")
        self.assertTrue(result)

    def test_cache_hash_mismatch(self):
        """Test that hash mismatch in cache returns True"""
        wrong_hash = hashlib.sha256(b"different").digest()
        self.tool._remote_file_cache["/test.txt"] = (len(self.test_data), wrong_hash)
        result = self.tool._file_needs_update(self.test_data, "/test.txt")
        self.assertTrue(result)

    def test_force_ignores_cache(self):
        """Test that force=True ignores cache"""
        tool = MpyTool(self.mock_conn, verbose=None, force=True)
        tool._mpy = Mock()
        tool._remote_file_cache["/test.txt"] = (len(self.test_data), self.test_hash)
        result = tool._file_needs_update(self.test_data, "/test.txt")
        self.assertTrue(result)

    def test_fallback_to_device_when_not_cached(self):
        """Test fallback to device calls when path not in cache"""
        self.tool._mpy = Mock()
        self.tool._mpy.stat.return_value = len(self.test_data)
        self.tool._mpy.hashfile.return_value = self.test_hash
        result = self.tool._file_needs_update(self.test_data, "/uncached.txt")
        self.assertFalse(result)
        self.tool._mpy.stat.assert_called_once_with("/uncached.txt")
        self.tool._mpy.hashfile.assert_called_once_with("/uncached.txt")


class TestPrefetchRemoteInfo(unittest.TestCase):
    """Tests for _prefetch_remote_info method"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()

    def test_prefetch_populates_cache(self):
        """Test that prefetch populates the cache"""
        self.tool._mpy.fileinfo.return_value = {
            "/a.txt": (100, b"hash_a"),
            "/b.txt": (200, b"hash_b"),
            "/c.txt": None,  # doesn't exist
        }
        self.tool._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200, "/c.txt": 300})
        self.assertEqual(self.tool._remote_file_cache["/a.txt"], (100, b"hash_a"))
        self.assertEqual(self.tool._remote_file_cache["/b.txt"], (200, b"hash_b"))
        self.assertIsNone(self.tool._remote_file_cache["/c.txt"])

    def test_prefetch_skips_cached_paths(self):
        """Test that already cached paths are not fetched again"""
        self.tool._remote_file_cache["/cached.txt"] = (50, b"cached_hash")
        self.tool._mpy.fileinfo.return_value = {"/new.txt": (100, b"new_hash")}
        self.tool._prefetch_remote_info({"/cached.txt": 50, "/new.txt": 100})
        # fileinfo should only be called with uncached path
        self.tool._mpy.fileinfo.assert_called_once_with({"/new.txt": 100})

    def test_prefetch_does_nothing_when_force(self):
        """Test that prefetch does nothing when force=True"""
        tool = MpyTool(self.mock_conn, verbose=None, force=True)
        tool._mpy = Mock()
        tool._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        tool._mpy.fileinfo.assert_not_called()

    def test_prefetch_handles_none_result(self):
        """Test that None result (hashlib unavailable) marks all as needing update"""
        self.tool._mpy.fileinfo.return_value = None
        self.tool._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        self.assertIsNone(self.tool._remote_file_cache["/a.txt"])
        self.assertIsNone(self.tool._remote_file_cache["/b.txt"])

    def test_prefetch_empty_dict(self):
        """Test that empty dict does nothing"""
        self.tool._prefetch_remote_info({})
        self.tool._mpy.fileinfo.assert_not_called()

    def test_prefetch_all_cached(self):
        """Test that no call is made when all paths are cached"""
        self.tool._remote_file_cache["/a.txt"] = (100, b"hash")
        self.tool._remote_file_cache["/b.txt"] = (200, b"hash")
        self.tool._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        self.tool._mpy.fileinfo.assert_not_called()

    def test_prefetch_passes_sizes_to_device(self):
        """Test that local sizes are passed to device for size comparison"""
        self.tool._mpy.fileinfo.return_value = {
            "/a.txt": (100, b"hash"),
            "/b.txt": (999, None),  # size mismatch, no hash computed
        }
        self.tool._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        # Verify sizes were passed
        self.tool._mpy.fileinfo.assert_called_once_with({"/a.txt": 100, "/b.txt": 200})


class TestResetBatchProgress(unittest.TestCase):
    """Tests for reset_batch_progress clearing cache"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)

    def test_clears_cache(self):
        """Test that reset_batch_progress clears the remote file cache"""
        self.tool._remote_file_cache["/a.txt"] = (100, b"hash")
        self.tool._remote_file_cache["/b.txt"] = (200, b"hash")
        self.tool.reset_batch_progress()
        self.assertEqual(self.tool._remote_file_cache, {})


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


class TestExcludeInCollect(unittest.TestCase):
    """Tests for exclude behavior in file collection methods"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        # Create temp directory structure
        self.temp_dir = tempfile.mkdtemp()
        # Create regular files
        with open(os.path.join(self.temp_dir, "main.py"), "w") as f:
            f.write("code")
        with open(os.path.join(self.temp_dir, "README.md"), "w") as f:
            f.write("docs")
        # Create hidden files
        with open(os.path.join(self.temp_dir, ".gitignore"), "w") as f:
            f.write("*.pyc")
        with open(os.path.join(self.temp_dir, ".DS_Store"), "w") as f:
            f.write("mac")
        # Create .pyc files
        with open(os.path.join(self.temp_dir, "module.pyc"), "w") as f:
            f.write("bytecode")
        # Create hidden directory
        hidden_dir = os.path.join(self.temp_dir, ".git")
        os.makedirs(hidden_dir)
        with open(os.path.join(hidden_dir, "config"), "w") as f:
            f.write("git config")
        # Create regular subdirectory
        subdir = os.path.join(self.temp_dir, "src")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "app.py"), "w") as f:
            f.write("app")
        with open(os.path.join(subdir, ".env"), "w") as f:
            f.write("secret")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_collect_dst_files_excludes_hidden(self):
        """Test that _collect_dst_files excludes hidden files and dirs"""
        files = self.tool._collect_dst_files(self.temp_dir, "/", add_src_basename=False)
        paths = list(files.keys())
        # Should include regular files
        self.assertIn("/main.py", paths)
        self.assertIn("/README.md", paths)
        self.assertIn("/src/app.py", paths)
        # Should exclude hidden files
        self.assertNotIn("/.gitignore", paths)
        self.assertNotIn("/.DS_Store", paths)
        self.assertNotIn("/src/.env", paths)
        # Should exclude .pyc files
        self.assertNotIn("/module.pyc", paths)
        # Should exclude .git contents
        self.assertNotIn("/.git/config", paths)

    def test_collect_local_paths_excludes_hidden(self):
        """Test that _collect_local_paths excludes hidden files and dirs"""
        paths = self.tool._collect_local_paths(self.temp_dir)
        path_names = [os.path.basename(p) for p in paths]
        # Should include regular files
        self.assertIn("main.py", path_names)
        self.assertIn("README.md", path_names)
        self.assertIn("app.py", path_names)
        # Should exclude hidden
        self.assertNotIn(".gitignore", path_names)
        self.assertNotIn(".DS_Store", path_names)
        self.assertNotIn(".env", path_names)
        self.assertNotIn("config", path_names)  # .git/config
        # Should exclude .pyc files
        self.assertNotIn("module.pyc", path_names)

    def test_collect_counts_only_included_files(self):
        """Test that file count matches only included files"""
        files = self.tool._collect_dst_files(self.temp_dir, "/", add_src_basename=False)
        paths = self.tool._collect_local_paths(self.temp_dir)
        # Both should return same count
        self.assertEqual(len(files), len(paths))
        # Should be exactly 3: main.py, README.md, src/app.py
        self.assertEqual(len(files), 3)


class TestCpPathCombinations(unittest.TestCase):
    """Tests for cp command path handling - all source/destination combinations.

    Path semantics:
    - ':' = CWD (current working directory on device, empty string)
    - ':/' = root directory
    - ':folder' = CWD/folder (name/rename)
    - ':folder/' = CWD/folder/ (directory, add basename)
    - ':/folder' = /folder (name/rename)
    - ':/folder/' = /folder/ (directory, add basename)
    - Trailing slash on source = copy contents (not the directory itself)
    - Multiple sources = same as 'dir/' (requires dest to be directory)
    """

    def setUp(self):
        self.mock_conn = Mock()
        # Use force=True to skip prefetch and simplify testing
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        # Mock _mpy for device operations
        self.tool._mpy = Mock()
        self.tool._mpy.stat.return_value = None  # File doesn't exist by default
        self.tool._mpy.put.return_value = ([], 0)  # (encodings, wire_bytes)
        self.tool._mpy.mkdir.return_value = None
        self.tool._mpy.import_module.return_value = None
        # Create temp directory structure
        self.temp_dir = tempfile.mkdtemp()
        # Create test directory with files
        self.test_dir = os.path.join(self.temp_dir, "adresar")
        os.makedirs(self.test_dir)
        with open(os.path.join(self.test_dir, "file1.py"), "w") as f:
            f.write("content1")
        with open(os.path.join(self.test_dir, "file2.py"), "w") as f:
            f.write("content2")
        # Create single test file
        self.test_file = os.path.join(self.temp_dir, "subor.py")
        with open(self.test_file, "w") as f:
            f.write("file content")
        # Create second test file for multiple sources
        self.test_file2 = os.path.join(self.temp_dir, "subor2.py")
        with open(self.test_file2, "w") as f:
            f.write("file2 content")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _get_put_destinations(self):
        """Extract destination paths from all put() calls"""
        return [call[0][1] for call in self.tool._mpy.put.call_args_list]

    def _get_mkdir_calls(self):
        """Extract paths from all mkdir() calls"""
        return [call[0][0] for call in self.tool._mpy.mkdir.call_args_list]

    # ========== Directory without trailing slash (copy directory as whole) ==========

    def test_dir_to_cwd(self):
        """'cp adresar :' -> CWD/adresar/..."""
        self.tool.cmd_cp(self.test_dir, ':')
        dests = self._get_put_destinations()
        self.assertTrue(all(d.startswith('adresar/') for d in dests))
        self.assertIn('adresar/file1.py', dests)
        self.assertIn('adresar/file2.py', dests)

    def test_dir_to_cwd_folder(self):
        """'cp adresar :folder' -> CWD/folder/..."""
        self.tool.cmd_cp(self.test_dir, ':folder')
        dests = self._get_put_destinations()
        self.assertTrue(all(d.startswith('folder/') for d in dests))
        self.assertIn('folder/file1.py', dests)
        self.assertIn('folder/file2.py', dests)

    def test_dir_to_cwd_folder_slash(self):
        """'cp adresar :folder/' -> CWD/folder/adresar/..."""
        self.tool.cmd_cp(self.test_dir, ':folder/')
        dests = self._get_put_destinations()
        self.assertTrue(all(d.startswith('folder/adresar/') for d in dests))
        self.assertIn('folder/adresar/file1.py', dests)
        self.assertIn('folder/adresar/file2.py', dests)

    def test_dir_to_root(self):
        """'cp adresar :/' -> /adresar/..."""
        self.tool.cmd_cp(self.test_dir, ':/')
        dests = self._get_put_destinations()
        self.assertTrue(all(d.startswith('/adresar/') for d in dests))
        self.assertIn('/adresar/file1.py', dests)
        self.assertIn('/adresar/file2.py', dests)

    def test_dir_to_root_folder(self):
        """'cp adresar :/folder' -> /folder/..."""
        self.tool.cmd_cp(self.test_dir, ':/folder')
        dests = self._get_put_destinations()
        self.assertTrue(all(d.startswith('/folder/') for d in dests))
        self.assertIn('/folder/file1.py', dests)
        self.assertIn('/folder/file2.py', dests)

    def test_dir_to_root_folder_slash(self):
        """'cp adresar :/folder/' -> /folder/adresar/..."""
        self.tool.cmd_cp(self.test_dir, ':/folder/')
        dests = self._get_put_destinations()
        self.assertTrue(all(d.startswith('/folder/adresar/') for d in dests))
        self.assertIn('/folder/adresar/file1.py', dests)
        self.assertIn('/folder/adresar/file2.py', dests)

    # ========== Directory with trailing slash (copy contents) ==========

    def test_dir_contents_to_cwd(self):
        """'cp adresar/ :' -> CWD/file1.py, CWD/file2.py"""
        self.tool.cmd_cp(self.test_dir + '/', ':')
        dests = self._get_put_destinations()
        self.assertIn('file1.py', dests)
        self.assertIn('file2.py', dests)
        # Should NOT have adresar prefix
        self.assertFalse(any('adresar' in d for d in dests))

    def test_dir_contents_to_cwd_folder_invalid(self):
        """'cp adresar/ :folder' -> INVALID (contents to non-directory)"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_cp(self.test_dir + '/', ':folder')
        self.assertIn('directory', str(ctx.exception).lower())

    def test_dir_contents_to_cwd_folder_slash(self):
        """'cp adresar/ :folder/' -> CWD/folder/file1.py, ..."""
        self.tool.cmd_cp(self.test_dir + '/', ':folder/')
        dests = self._get_put_destinations()
        self.assertIn('folder/file1.py', dests)
        self.assertIn('folder/file2.py', dests)

    def test_dir_contents_to_root(self):
        """'cp adresar/ :/' -> /file1.py, /file2.py"""
        self.tool.cmd_cp(self.test_dir + '/', ':/')
        dests = self._get_put_destinations()
        self.assertIn('/file1.py', dests)
        self.assertIn('/file2.py', dests)

    def test_dir_contents_to_root_folder_invalid(self):
        """'cp adresar/ :/folder' -> INVALID (contents to non-directory)"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_cp(self.test_dir + '/', ':/folder')
        self.assertIn('directory', str(ctx.exception).lower())

    def test_dir_contents_to_root_folder_slash(self):
        """'cp adresar/ :/folder/' -> /folder/file1.py, ..."""
        self.tool.cmd_cp(self.test_dir + '/', ':/folder/')
        dests = self._get_put_destinations()
        self.assertIn('/folder/file1.py', dests)
        self.assertIn('/folder/file2.py', dests)

    # ========== Single file ==========

    def test_file_to_cwd(self):
        """'cp subor.py :' -> CWD/subor.py"""
        self.tool.cmd_cp(self.test_file, ':')
        dests = self._get_put_destinations()
        self.assertEqual(dests, ['subor.py'])

    def test_file_to_cwd_renamed(self):
        """'cp subor.py :file.py' -> CWD/file.py"""
        self.tool.cmd_cp(self.test_file, ':file.py')
        dests = self._get_put_destinations()
        self.assertEqual(dests, ['file.py'])

    def test_file_to_cwd_folder_slash(self):
        """'cp subor.py :folder/' -> CWD/folder/subor.py"""
        self.tool.cmd_cp(self.test_file, ':folder/')
        dests = self._get_put_destinations()
        self.assertEqual(dests, ['folder/subor.py'])

    def test_file_to_root(self):
        """'cp subor.py :/' -> /subor.py"""
        self.tool.cmd_cp(self.test_file, ':/')
        dests = self._get_put_destinations()
        self.assertEqual(dests, ['/subor.py'])

    def test_file_to_root_renamed(self):
        """'cp subor.py :/file.py' -> /file.py"""
        self.tool.cmd_cp(self.test_file, ':/file.py')
        dests = self._get_put_destinations()
        self.assertEqual(dests, ['/file.py'])

    def test_file_to_root_folder_slash(self):
        """'cp subor.py :/folder/' -> /folder/subor.py"""
        self.tool.cmd_cp(self.test_file, ':/folder/')
        dests = self._get_put_destinations()
        self.assertEqual(dests, ['/folder/subor.py'])

    # ========== Multiple sources (same rules as dir/) ==========

    def test_multiple_files_to_cwd(self):
        """'cp file1 file2 :' -> CWD/file1, CWD/file2"""
        self.tool.cmd_cp(self.test_file, self.test_file2, ':')
        dests = self._get_put_destinations()
        self.assertIn('subor.py', dests)
        self.assertIn('subor2.py', dests)

    def test_multiple_files_to_cwd_folder_invalid(self):
        """'cp file1 file2 :folder' -> INVALID"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_cp(self.test_file, self.test_file2, ':folder')
        self.assertIn('directory', str(ctx.exception).lower())

    def test_multiple_files_to_cwd_folder_slash(self):
        """'cp file1 file2 :folder/' -> CWD/folder/file1, ..."""
        self.tool.cmd_cp(self.test_file, self.test_file2, ':folder/')
        dests = self._get_put_destinations()
        self.assertIn('folder/subor.py', dests)
        self.assertIn('folder/subor2.py', dests)

    def test_multiple_files_to_root(self):
        """'cp file1 file2 :/' -> /file1, /file2"""
        self.tool.cmd_cp(self.test_file, self.test_file2, ':/')
        dests = self._get_put_destinations()
        self.assertIn('/subor.py', dests)
        self.assertIn('/subor2.py', dests)

    def test_multiple_files_to_root_folder_invalid(self):
        """'cp file1 file2 :/folder' -> INVALID"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_cp(self.test_file, self.test_file2, ':/folder')
        self.assertIn('directory', str(ctx.exception).lower())

    def test_multiple_files_to_root_folder_slash(self):
        """'cp file1 file2 :/folder/' -> /folder/file1, ..."""
        self.tool.cmd_cp(self.test_file, self.test_file2, ':/folder/')
        dests = self._get_put_destinations()
        self.assertIn('/folder/subor.py', dests)
        self.assertIn('/folder/subor2.py', dests)


class TestCpRemoteToLocalPathCombinations(unittest.TestCase):
    """Tests for cp command path handling - remote to local (download).

    Path semantics for remote source:
    - ':adresar' = copy directory as whole
    - ':adresar/' = copy contents (not the directory itself)
    - ':subor.py' = copy file

    Path semantics for local destination:
    - '.' or './' = CWD (directory)
    - 'folder/' = directory (add basename)
    - 'folder' = if exists as dir -> directory, else rename
    - Trailing slash on remote source + non-dir local dest = INVALID
    """

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None, force=True)
        self.tool._mpy = Mock()
        self.tool._mpy.import_module.return_value = None
        # Create temp directory for local destination
        self.temp_dir = tempfile.mkdtemp()
        # Create a subdirectory to test "exists as dir" logic
        self.existing_dir = os.path.join(self.temp_dir, "existing_dir")
        os.makedirs(self.existing_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _setup_remote_file(self, path, content=b"test content"):
        """Setup mock for a remote file (path without : prefix)"""
        # Strip : prefix if present
        path = path[1:] if path.startswith(':') else path
        self.tool._mpy.stat.return_value = len(content)
        self.tool._mpy.get.return_value = content

    def _setup_remote_dir(self, path, files):
        """Setup mock for a remote directory with files

        path: remote path (with or without : prefix)
        files: list of (name, size) tuples
        """
        # Strip : prefix if present
        path = path[1:] if path.startswith(':') else path
        path = path.rstrip('/')

        def stat_side_effect(p):
            p = p.rstrip('/')
            if p == path:
                return -1  # directory
            for name, size in files:
                if p == path + '/' + name:
                    return size
            return None
        self.tool._mpy.stat.side_effect = stat_side_effect
        self.tool._mpy.ls.return_value = files
        self.tool._mpy.get.return_value = b"file content"

    def _get_written_files(self):
        """Get list of local files written (from open() calls via _get_file)"""
        # Files are written via open() in _get_file, track via get() calls and dst paths
        # Since we mock get(), we need to track what paths were used
        return []

    # ========== Single file downloads ==========

    def test_file_to_cwd(self):
        """'cp :subor.py .' -> ./subor.py"""
        self._setup_remote_file(':subor.py')
        dst = os.path.join(self.temp_dir, '.')
        self.tool.cmd_cp(':subor.py', dst)
        # Check that file was downloaded
        self.tool._mpy.get.assert_called()

    def test_file_to_folder_slash(self):
        """'cp :subor.py folder/' -> folder/subor.py"""
        self._setup_remote_file(':subor.py')
        dst = os.path.join(self.temp_dir, 'newfolder/')
        self.tool.cmd_cp(':subor.py', dst)
        self.tool._mpy.get.assert_called()

    def test_file_renamed(self):
        """'cp :subor.py newname.py' -> newname.py (rename)"""
        self._setup_remote_file(':subor.py')
        dst = os.path.join(self.temp_dir, 'newname.py')
        self.tool.cmd_cp(':subor.py', dst)
        self.tool._mpy.get.assert_called()

    def test_file_to_existing_dir(self):
        """'cp :subor.py existing_dir' -> existing_dir/subor.py"""
        self._setup_remote_file(':subor.py')
        self.tool.cmd_cp(':subor.py', self.existing_dir)
        self.tool._mpy.get.assert_called()

    # ========== Directory downloads ==========

    def test_dir_to_folder_slash(self):
        """'cp :adresar folder/' -> folder/adresar/..."""
        self._setup_remote_dir(':adresar', [('file1.py', 10), ('file2.py', 20)])
        dst = os.path.join(self.temp_dir, 'folder/')
        self.tool.cmd_cp(':adresar', dst)
        self.tool._mpy.ls.assert_called()

    def test_dir_contents_to_folder_slash(self):
        """'cp :adresar/ folder/' -> folder/file1.py, folder/file2.py"""
        self._setup_remote_dir(':adresar', [('file1.py', 10), ('file2.py', 20)])
        dst = os.path.join(self.temp_dir, 'folder/')
        self.tool.cmd_cp(':adresar/', dst)
        self.tool._mpy.ls.assert_called()

    def test_dir_contents_to_non_dir_invalid(self):
        """'cp :adresar/ newname' -> INVALID (contents to non-directory)"""
        self._setup_remote_dir(':adresar', [('file1.py', 10), ('file2.py', 20)])
        dst = os.path.join(self.temp_dir, 'newname')  # doesn't exist, no trailing /
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_cp(':adresar/', dst)
        self.assertIn('directory', str(ctx.exception).lower())

    # ========== Multiple remote sources ==========

    def test_multiple_files_to_folder_slash(self):
        """'cp :file1.py :file2.py folder/' -> folder/file1.py, folder/file2.py"""
        self.tool._mpy.stat.return_value = 10  # file size
        self.tool._mpy.get.return_value = b"content"
        dst = os.path.join(self.temp_dir, 'folder/')
        self.tool.cmd_cp(':file1.py', ':file2.py', dst)
        self.assertEqual(self.tool._mpy.get.call_count, 2)

    def test_multiple_files_to_non_dir_invalid(self):
        """'cp :file1.py :file2.py newname' -> INVALID"""
        self.tool._mpy.stat.return_value = 10
        dst = os.path.join(self.temp_dir, 'newname')  # doesn't exist
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_cp(':file1.py', ':file2.py', dst)
        self.assertIn('directory', str(ctx.exception).lower())


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
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_mv(':a.py', ':b.py', ':newname')
        self.assertIn('directory', str(ctx.exception).lower())

    # ========== Error cases ==========

    def test_missing_colon_prefix_source(self):
        """'mv file.py :/new.py' -> INVALID (source must have : prefix)"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_mv('file.py', ':/new.py')
        self.assertIn('device path', str(ctx.exception).lower())

    def test_missing_colon_prefix_dest(self):
        """'mv :file.py new.py' -> INVALID (dest must have : prefix)"""
        from mpytool.mpytool import ParamsError
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_mv(':file.py', 'new.py')
        self.assertIn('device path', str(ctx.exception).lower())

    def test_source_not_found(self):
        """'mv :nonexistent.py :/new.py' -> INVALID (source not found)"""
        self.tool._mpy.stat.return_value = None  # File doesn't exist
        from mpytool.mpytool import ParamsError
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
        from mpytool.mpytool import ParamsError
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


if __name__ == "__main__":
    unittest.main()
