"""Tests for MpyTool class (unit tests without device)"""

import os
import tempfile
import shutil
import hashlib
import unittest
from unittest.mock import Mock, patch, MagicMock

from mpytool.mpytool import MpyTool


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


if __name__ == "__main__":
    unittest.main()
