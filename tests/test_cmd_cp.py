"""Tests for CopyCommand class (cp command - unit tests without device)"""

import fnmatch
import hashlib
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from mpytool.mpytool import MpyTool, ParamsError
from mpytool.cmd_cp import CopyCommand
from mpytool.mpy_cross import MpyCross


def _is_excluded_default(name):
    """Default exclusion check for tests"""
    patterns = {'.*', '*.pyc', '__pycache__'}
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


class TestCollectDstFiles(unittest.TestCase):
    """Tests for _collect_dst_files method in CopyCommand"""

    def setUp(self):
        # Create a mock mpy instance
        self.mock_mpy = Mock()
        self.mock_log = Mock(_loglevel=1)
        self.copy_cmd = CopyCommand(
            self.mock_mpy, self.mock_log,
            lambda *a, **kw: None, lambda: False, lambda: 1,
            is_excluded_fn=_is_excluded_default)
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
        files = self.copy_cmd._collect_dst_files(self.test_file, "/")
        self.assertEqual(files, {"/file.txt": 12})

    def test_single_file_to_dir(self):
        """Test collecting path and size for single file to directory"""
        files = self.copy_cmd._collect_dst_files(self.test_file, "/dest/")
        self.assertEqual(files, {"/dest/file.txt": 12})

    def test_single_file_with_explicit_name(self):
        """Test collecting path and size for single file with explicit destination name"""
        files = self.copy_cmd._collect_dst_files(self.test_file, "/dest/renamed.txt")
        self.assertEqual(files, {"/dest/renamed.txt": 12})

    def test_directory_with_basename(self):
        """Test collecting paths and sizes for directory (adds src basename)"""
        files = self.copy_cmd._collect_dst_files(self.test_subdir, "/", add_src_basename=True)
        self.assertEqual(files["/subdir/a.txt"], 1)
        self.assertEqual(files["/subdir/b.txt"], 2)
        self.assertEqual(files["/subdir/nested/c.txt"], 3)
        self.assertEqual(len(files), 3)

    def test_directory_without_basename(self):
        """Test collecting paths and sizes for directory contents (no src basename)"""
        files = self.copy_cmd._collect_dst_files(self.test_subdir, "/dest", add_src_basename=False)
        self.assertEqual(files["/dest/a.txt"], 1)
        self.assertEqual(files["/dest/b.txt"], 2)
        self.assertEqual(files["/dest/nested/c.txt"], 3)
        self.assertEqual(len(files), 3)

    def test_excludes_pyc_files(self):
        """Test that *.pyc files are excluded"""
        pyc_file = os.path.join(self.test_subdir, "module.pyc")
        with open(pyc_file, "w") as f:
            f.write("bytecode")
        files = self.copy_cmd._collect_dst_files(self.test_subdir, "/", add_src_basename=True)
        self.assertNotIn("/subdir/module.pyc", files)
        self.assertEqual(len(files), 3)  # only a.txt, b.txt, nested/c.txt

    def test_nonexistent_path(self):
        """Test that nonexistent path returns empty dict"""
        files = self.copy_cmd._collect_dst_files("/nonexistent/path", "/")
        self.assertEqual(files, {})


class TestFileNeedsUpdateWithCache(unittest.TestCase):
    """Tests for _file_needs_update with cache in CopyCommand"""

    def setUp(self):
        self.mock_mpy = Mock()
        self.mock_log = Mock(_loglevel=1)
        self.copy_cmd = CopyCommand(
            self.mock_mpy, self.mock_log,
            lambda *a, **kw: None, lambda: False, lambda: 1)
        self.test_data = b"test content"
        self.test_hash = hashlib.sha256(self.test_data).digest()

    def test_uses_cache_when_available(self):
        """Test that cache is used instead of device calls"""
        # Pre-populate cache
        self.copy_cmd._remote_file_cache["/test.txt"] = (len(self.test_data), self.test_hash)
        # Should return False (file unchanged) without any device calls
        result = self.copy_cmd._file_needs_update(self.test_data, "/test.txt")
        self.assertFalse(result)
        # Verify no device calls were made
        self.mock_mpy.stat.assert_not_called()
        self.mock_mpy.hashfile.assert_not_called()

    def test_cache_none_means_needs_update(self):
        """Test that None in cache means file doesn't exist"""
        self.copy_cmd._remote_file_cache["/missing.txt"] = None
        result = self.copy_cmd._file_needs_update(self.test_data, "/missing.txt")
        self.assertTrue(result)

    def test_cache_size_mismatch(self):
        """Test that size mismatch in cache returns True"""
        self.copy_cmd._remote_file_cache["/test.txt"] = (999, self.test_hash)
        result = self.copy_cmd._file_needs_update(self.test_data, "/test.txt")
        self.assertTrue(result)

    def test_cache_hash_mismatch(self):
        """Test that hash mismatch in cache returns True"""
        wrong_hash = hashlib.sha256(b"different").digest()
        self.copy_cmd._remote_file_cache["/test.txt"] = (len(self.test_data), wrong_hash)
        result = self.copy_cmd._file_needs_update(self.test_data, "/test.txt")
        self.assertTrue(result)

    def test_force_ignores_cache(self):
        """Test that force=True ignores cache"""
        copy_cmd = CopyCommand(
            self.mock_mpy, self.mock_log,
            lambda *a, **kw: None, lambda: False, lambda: 1)
        copy_cmd._force = True
        copy_cmd._remote_file_cache["/test.txt"] = (len(self.test_data), self.test_hash)
        result = copy_cmd._file_needs_update(self.test_data, "/test.txt")
        self.assertTrue(result)

    def test_fallback_to_device_when_not_cached(self):
        """Test fallback to device calls when path not in cache"""
        self.mock_mpy.stat.return_value = len(self.test_data)
        self.mock_mpy.hashfile.return_value = self.test_hash
        result = self.copy_cmd._file_needs_update(self.test_data, "/uncached.txt")
        self.assertFalse(result)
        self.mock_mpy.stat.assert_called_once_with("/uncached.txt")
        self.mock_mpy.hashfile.assert_called_once_with("/uncached.txt")


class TestPrefetchRemoteInfo(unittest.TestCase):
    """Tests for _prefetch_remote_info method in CopyCommand"""

    def setUp(self):
        self.mock_mpy = Mock()
        self.mock_log = Mock(_loglevel=1)
        self.copy_cmd = CopyCommand(
            self.mock_mpy, self.mock_log,
            lambda *a, **kw: None, lambda: False, lambda: 1)

    def test_prefetch_populates_cache(self):
        """Test that prefetch populates the cache"""
        self.mock_mpy.fileinfo.return_value = {
            "/a.txt": (100, b"hash_a"),
            "/b.txt": (200, b"hash_b"),
            "/c.txt": None,  # doesn't exist
        }
        self.copy_cmd._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200, "/c.txt": 300})
        self.assertEqual(self.copy_cmd._remote_file_cache["/a.txt"], (100, b"hash_a"))
        self.assertEqual(self.copy_cmd._remote_file_cache["/b.txt"], (200, b"hash_b"))
        self.assertIsNone(self.copy_cmd._remote_file_cache["/c.txt"])

    def test_prefetch_skips_cached_paths(self):
        """Test that already cached paths are not fetched again"""
        self.copy_cmd._remote_file_cache["/cached.txt"] = (50, b"cached_hash")
        self.mock_mpy.fileinfo.return_value = {"/new.txt": (100, b"new_hash")}
        self.copy_cmd._prefetch_remote_info({"/cached.txt": 50, "/new.txt": 100})
        # fileinfo should only be called with uncached path
        self.mock_mpy.fileinfo.assert_called_once_with({"/new.txt": 100})

    def test_prefetch_does_nothing_when_force(self):
        """Test that prefetch does nothing when force=True"""
        copy_cmd = CopyCommand(
            self.mock_mpy, self.mock_log,
            lambda *a, **kw: None, lambda: False, lambda: 1)
        copy_cmd._force = True
        copy_cmd._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        self.mock_mpy.fileinfo.assert_not_called()

    def test_prefetch_handles_none_result(self):
        """Test that None result (hashlib unavailable) marks all as needing update"""
        self.mock_mpy.fileinfo.return_value = None
        self.copy_cmd._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        self.assertIsNone(self.copy_cmd._remote_file_cache["/a.txt"])
        self.assertIsNone(self.copy_cmd._remote_file_cache["/b.txt"])

    def test_prefetch_empty_dict(self):
        """Test that empty dict does nothing"""
        self.copy_cmd._prefetch_remote_info({})
        self.mock_mpy.fileinfo.assert_not_called()

    def test_prefetch_all_cached(self):
        """Test that no call is made when all paths are cached"""
        self.copy_cmd._remote_file_cache["/a.txt"] = (100, b"hash")
        self.copy_cmd._remote_file_cache["/b.txt"] = (200, b"hash")
        self.copy_cmd._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        self.mock_mpy.fileinfo.assert_not_called()

    def test_prefetch_passes_sizes_to_device(self):
        """Test that local sizes are passed to device for size comparison"""
        self.mock_mpy.fileinfo.return_value = {
            "/a.txt": (100, b"hash"),
            "/b.txt": (999, None),  # size mismatch, no hash computed
        }
        self.copy_cmd._prefetch_remote_info({"/a.txt": 100, "/b.txt": 200})
        # Verify sizes were passed
        self.mock_mpy.fileinfo.assert_called_once_with({"/a.txt": 100, "/b.txt": 200})


class TestResetBatchProgress(unittest.TestCase):
    """Tests for reset_batch_progress clearing cache in CopyCommand"""

    def setUp(self):
        self.mock_mpy = Mock()
        self.mock_log = Mock(_loglevel=1)
        self.copy_cmd = CopyCommand(
            self.mock_mpy, self.mock_log,
            lambda *a, **kw: None, lambda: False, lambda: 1)

    def test_clears_cache(self):
        """Test that reset_batch_progress clears the remote file cache"""
        self.copy_cmd._remote_file_cache["/a.txt"] = (100, b"hash")
        self.copy_cmd._remote_file_cache["/b.txt"] = (200, b"hash")
        self.copy_cmd.reset_batch_progress()
        self.assertEqual(self.copy_cmd._remote_file_cache, {})


class TestExcludeInCollect(unittest.TestCase):
    """Tests for exclude behavior in file collection methods in CopyCommand"""

    def setUp(self):
        self.mock_mpy = Mock()
        self.mock_log = Mock(_loglevel=1)
        self.copy_cmd = CopyCommand(
            self.mock_mpy, self.mock_log,
            lambda *a, **kw: None, lambda: False, lambda: 1,
            is_excluded_fn=_is_excluded_default)
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
        files = self.copy_cmd._collect_dst_files(self.temp_dir, "/", add_src_basename=False)
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
        paths = self.copy_cmd._collect_local_paths(self.temp_dir)
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
        files = self.copy_cmd._collect_dst_files(self.temp_dir, "/", add_src_basename=False)
        paths = self.copy_cmd._collect_local_paths(self.temp_dir)
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
        with self.assertRaises(ParamsError) as ctx:
            self.tool.cmd_cp(':file1.py', ':file2.py', dst)
        self.assertIn('directory', str(ctx.exception).lower())


class TestMpyCompilation(unittest.TestCase):
    """Tests for --mpy compilation feature (MpyCross class + CopyCommand integration)"""

    def setUp(self):
        self.mock_conn = Mock()
        self.tool = MpyTool(self.mock_conn, verbose=None)
        self.tool._mpy = Mock()
        self.compiler = MpyCross(self.tool._log, self.tool.verbose)
        self.tool._mpy_cross = self.compiler
        self.temp_dir = tempfile.mkdtemp()
        # Create test .py files
        self.script_py = os.path.join(self.temp_dir, 'script.py')
        with open(self.script_py, 'w') as f:
            f.write('print("hello")\n')
        self.boot_py = os.path.join(self.temp_dir, 'boot.py')
        with open(self.boot_py, 'w') as f:
            f.write('# boot\n')
        self.main_py = os.path.join(self.temp_dir, 'main.py')
        with open(self.main_py, 'w') as f:
            f.write('# main\n')
        self.data_json = os.path.join(self.temp_dir, 'data.json')
        with open(self.data_json, 'w') as f:
            f.write('{}')
        # Subdirectory with .py file
        self.sub_dir = os.path.join(self.temp_dir, 'lib')
        os.makedirs(self.sub_dir)
        self.lib_py = os.path.join(self.sub_dir, 'mylib.py')
        with open(self.lib_py, 'w') as f:
            f.write('x = 1\n')

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _setup_compiler(self, arch=None):
        """Setup compiler with active state and fake version"""
        self.compiler.active = True
        self.compiler._ver = (6, 3)
        self.compiler._arch = arch
        self.compiler._args = []

    def test_compile_skip_boot_py(self):
        """boot.py should not be compiled"""
        self._setup_compiler()
        result = self.compiler.compile(self.boot_py)
        self.assertIsNone(result)
        self.assertNotIn(self.boot_py, self.compiler.compiled)

    def test_compile_skip_main_py(self):
        """main.py should not be compiled"""
        self._setup_compiler()
        result = self.compiler.compile(self.main_py)
        self.assertIsNone(result)
        self.assertNotIn(self.main_py, self.compiler.compiled)

    def test_compile_skip_non_py(self):
        """Non-.py files should not be compiled"""
        self._setup_compiler()
        result = self.compiler.compile(self.data_json)
        self.assertIsNone(result)

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_compile_creates_cache(self, mock_run):
        """Compilation creates __pycache__/name.mpy-X.Y.mpy"""
        self._setup_compiler()
        mock_run.return_value = Mock(returncode=0, stderr='', stdout='')
        result = self.compiler.compile(self.script_py)
        expected_cache = os.path.join(
            self.temp_dir, '__pycache__', 'script.mpy-6.3.mpy')
        self.assertEqual(result, expected_cache)
        self.assertEqual(self.compiler.compiled[self.script_py], expected_cache)
        mock_run.assert_called_once_with(
            ['mpy-cross', '-o', expected_cache, self.script_py],
            capture_output=True, text=True, timeout=30)

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_compile_cache_includes_arch(self, mock_run):
        """Cache path includes architecture for native/viper support"""
        self._setup_compiler(arch='xtensawin')
        self.compiler._args = ['-march=xtensawin']
        mock_run.return_value = Mock(returncode=0, stderr='', stdout='')
        result = self.compiler.compile(self.script_py)
        expected_cache = os.path.join(
            self.temp_dir, '__pycache__', 'script.mpy-6.3-xtensawin.mpy')
        self.assertEqual(result, expected_cache)
        mock_run.assert_called_once_with(
            ['mpy-cross', '-march=xtensawin', '-o',
                expected_cache, self.script_py],
            capture_output=True, text=True, timeout=30)

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_compile_uses_b_flag_on_mismatch(self, mock_run):
        """Compilation uses -b flag when mpy-cross version differs from device"""
        self._setup_compiler()
        self.compiler._args = ['-b', '6.1']
        self.compiler._ver = (6, 1)
        mock_run.return_value = Mock(returncode=0, stderr='', stdout='')
        cache_path = os.path.join(
            self.temp_dir, '__pycache__', 'script.mpy-6.1.mpy')
        self.compiler.compile(self.script_py)
        mock_run.assert_called_once_with(
            ['mpy-cross', '-b', '6.1', '-o', cache_path, self.script_py],
            capture_output=True, text=True, timeout=30)

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_compile_uses_cache_when_fresh(self, mock_run):
        """Fresh cache file should be reused without recompilation"""
        self._setup_compiler()
        cache_dir = os.path.join(self.temp_dir, '__pycache__')
        os.makedirs(cache_dir)
        cache_path = os.path.join(cache_dir, 'script.mpy-6.3.mpy')
        with open(cache_path, 'wb') as f:
            f.write(b'\x4d\x06\x03')
        import time
        future = time.time() + 10
        os.utime(cache_path, (future, future))
        result = self.compiler.compile(self.script_py)
        self.assertEqual(result, cache_path)
        mock_run.assert_not_called()

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_compile_recompiles_stale_cache(self, mock_run):
        """Stale cache file should trigger recompilation"""
        self._setup_compiler()
        cache_dir = os.path.join(self.temp_dir, '__pycache__')
        os.makedirs(cache_dir)
        cache_path = os.path.join(cache_dir, 'script.mpy-6.3.mpy')
        with open(cache_path, 'wb') as f:
            f.write(b'\x4d\x06\x03')
        import time
        future = time.time() + 10
        os.utime(self.script_py, (future, future))
        mock_run.return_value = Mock(returncode=0, stderr='', stdout='')
        result = self.compiler.compile(self.script_py)
        self.assertEqual(result, cache_path)
        mock_run.assert_called_once()

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_compile_failure_returns_none(self, mock_run):
        """mpy-cross failure should return None (upload .py instead)"""
        self._setup_compiler()
        self.compiler._log = Mock()
        mock_run.return_value = Mock(
            returncode=1, stderr='SyntaxError: invalid syntax', stdout='')
        result = self.compiler.compile(self.script_py)
        self.assertIsNone(result)
        self.assertNotIn(self.script_py, self.compiler.compiled)

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_compile_sources_walks_directory(self, mock_run):
        """compile_sources should compile all .py in directory"""
        self._setup_compiler()
        mock_run.return_value = Mock(returncode=0, stderr='', stdout='')
        self.compiler.compile_sources(
            [self.temp_dir], self.tool._is_excluded)
        compiled_basenames = {
            os.path.basename(p) for p in self.compiler.compiled}
        self.assertIn('script.py', compiled_basenames)
        self.assertIn('mylib.py', compiled_basenames)
        self.assertNotIn('boot.py', compiled_basenames)
        self.assertNotIn('main.py', compiled_basenames)

    @patch('mpytool.mpy_cross._subprocess.run')
    def test_collect_dst_files_with_mpy(self, mock_run):
        """_collect_dst_files should use .mpy paths and compiled sizes"""
        self._setup_compiler()
        mock_run.return_value = Mock(returncode=0, stderr='', stdout='')
        self.compiler.compile_sources(
            [self.temp_dir], self.tool._is_excluded)
        # Create fake cache files with known sizes
        for src, cache in self.compiler.compiled.items():
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, 'wb') as f:
                f.write(b'\x00' * 42)
        # Use CopyCommand with mpy_cross
        copy_cmd = CopyCommand(
            self.tool._mpy, self.tool._log,
            self.tool.verbose, lambda: False, lambda: 1,
            is_excluded_fn=self.tool._is_excluded)
        copy_cmd._mpy_cross = self.compiler
        files = copy_cmd._collect_dst_files(self.temp_dir, '/', add_src_basename=False)
        py_paths = [p for p in files if p.endswith('.py')]
        mpy_paths = [p for p in files if p.endswith('.mpy')]
        self.assertEqual(
            sorted(py_paths), sorted(['/boot.py', '/main.py']))
        self.assertEqual(
            sorted(mpy_paths), sorted(['/lib/mylib.mpy', '/script.mpy']))
        for p in mpy_paths:
            self.assertEqual(files[p], 42)

    def test_upload_file_uses_compiled_data(self):
        """_upload_file should use compiled data when available"""
        self._setup_compiler()
        cache_dir = os.path.join(self.temp_dir, '__pycache__')
        os.makedirs(cache_dir)
        cache_path = os.path.join(cache_dir, 'script.mpy-6.3.mpy')
        compiled_data = b'\x4d\x06\x03compiled'
        with open(cache_path, 'wb') as f:
            f.write(compiled_data)
        self.compiler.compiled[self.script_py] = cache_path
        self.tool._mpy.put.return_value = (set(), 0)
        # Use CopyCommand with mpy_cross
        copy_cmd = CopyCommand(
            self.tool._mpy, self.tool._log,
            self.tool.verbose, lambda: False, lambda: 0,
            is_excluded_fn=self.tool._is_excluded)
        copy_cmd._mpy_cross = self.compiler
        copy_cmd._force = True
        copy_cmd._upload_file(
            b'original .py data', self.script_py, '/script.py', False)
        call_args = self.tool._mpy.put.call_args
        self.assertEqual(call_args[0][0], compiled_data)
        self.assertEqual(call_args[0][1], '/script.mpy')

    @patch('mpytool.mpy_cross._shutil.which', return_value='/usr/bin/mpy-cross')
    @patch('mpytool.mpy_cross._subprocess.run')
    def test_init_version_match(self, mock_run, mock_which):
        """init should succeed when versions match"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='MicroPython v1.24.0 on 2024-10-01; mpy-cross emitting mpy v6.3\n',
            stderr='')
        self.compiler.init({
            'mpy_ver': 6, 'mpy_sub': 3, 'mpy_arch': 4,
            'version': '1.24.0'})
        self.assertTrue(self.compiler.active)
        self.assertEqual(self.compiler._ver, (6, 3))
        self.assertIn('-march=armv6m', self.compiler._args)

    @patch('mpytool.mpy_cross._shutil.which', return_value='/usr/bin/mpy-cross')
    @patch('mpytool.mpy_cross._subprocess.run')
    def test_init_version_mismatch_uses_b_flag(self, mock_run, mock_which):
        """init should use -b flag on version mismatch"""
        mock_run.return_value = Mock(
            returncode=0,
            stdout='MicroPython v1.24.0; mpy-cross emitting mpy v6.3\n',
            stderr='')
        self.compiler.init({
            'mpy_ver': 6, 'mpy_sub': 1, 'mpy_arch': 10,
            'version': '1.20.0'})
        self.assertTrue(self.compiler.active)
        self.assertEqual(self.compiler._ver, (6, 1))
        self.assertIn('-b', self.compiler._args)
        self.assertIn('6.1', self.compiler._args)
        self.assertIn('-march=xtensawin', self.compiler._args)

    @patch('mpytool.mpy_cross._shutil.which', return_value=None)
    def test_init_not_found(self, mock_which):
        """init should deactivate when mpy-cross not in PATH"""
        self.compiler._log = Mock()
        self.compiler.init({'mpy_ver': 6, 'mpy_sub': 3, 'version': '1.24.0'})
        self.assertFalse(self.compiler.active)

    @patch('mpytool.cmd_cp.CopyCommand.run')
    def test_cmd_cp_mpy_flag_parsing(self, mock_run):
        """cmd_cp should parse --mpy flag"""
        # cmd_cp creates CopyCommand and calls run() with mpy=True
        self.tool.cmd_cp('--mpy', 'src', ':dst')
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        self.assertTrue(call_kwargs['mpy'])

    @patch('mpytool.cmd_cp.CopyCommand.run')
    def test_cmd_cp_combined_flags(self, mock_run):
        """cmd_cp should parse combined flags like -fm"""
        self.tool.cmd_cp('-fm', 'src', ':dst')
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        self.assertTrue(call_kwargs['force'])
        self.assertTrue(call_kwargs['mpy'])
        # After cmd_cp finishes (even with mock), flags should be restored


if __name__ == "__main__":
    unittest.main()
