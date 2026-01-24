"""Integration tests requiring connected MicroPython device

Set MPYTOOL_TEST_PORT environment variable to run these tests:
    MPYTOOL_TEST_PORT=/dev/ttyACM0 python -m unittest tests.test_integration -v
"""

import os
import unittest

# Skip all tests if no device connected
DEVICE_PORT = os.environ.get("MPYTOOL_TEST_PORT")


def requires_device(cls):
    """Decorator to skip test class if no device is connected"""
    if not DEVICE_PORT:
        return unittest.skip("MPYTOOL_TEST_PORT not set")(cls)
    return cls


@requires_device
class TestDeviceConnection(unittest.TestCase):
    """Test basic device connection"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()

    def test_connection_established(self):
        """Test that connection is established"""
        self.assertIsNotNone(self.conn)

    def test_enter_raw_repl(self):
        """Test entering raw REPL mode"""
        self.mpy.comm.enter_raw_repl()
        self.assertTrue(self.mpy.comm._repl_mode)


@requires_device
class TestFileOperations(unittest.TestCase):
    """Test file operations on device"""

    TEST_DIR = "/_mpytool_test"
    TEST_FILE = "/_mpytool_test/test.txt"
    TEST_CONTENT = b"Hello from mpytool test!"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        # Cleanup test directory
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()

    def test_01_mkdir(self):
        """Test creating directory"""
        self.mpy.mkdir(self.TEST_DIR)
        result = self.mpy.stat(self.TEST_DIR)
        self.assertEqual(result, -1)  # -1 means directory

    def test_02_put(self):
        """Test uploading file"""
        self.mpy.put(self.TEST_CONTENT, self.TEST_FILE)
        result = self.mpy.stat(self.TEST_FILE)
        self.assertEqual(result, len(self.TEST_CONTENT))

    def test_03_get(self):
        """Test downloading file"""
        content = self.mpy.get(self.TEST_FILE)
        self.assertEqual(content, self.TEST_CONTENT)

    def test_04_ls(self):
        """Test listing directory"""
        result = self.mpy.ls(self.TEST_DIR)
        names = [name for name, size in result]
        self.assertIn("test.txt", names)

    def test_05_tree(self):
        """Test tree listing"""
        path, size, children = self.mpy.tree(self.TEST_DIR)
        self.assertEqual(path, self.TEST_DIR)
        self.assertIsInstance(children, list)

    def test_05a_stat_file(self):
        """Test stat on file returns size"""
        result = self.mpy.stat(self.TEST_FILE)
        self.assertIsInstance(result, int)
        self.assertEqual(result, len(self.TEST_CONTENT))

    def test_05b_stat_dir(self):
        """Test stat on directory returns -1"""
        result = self.mpy.stat(self.TEST_DIR)
        self.assertEqual(result, -1)

    def test_05c_stat_nonexistent(self):
        """Test stat on nonexistent path returns None"""
        result = self.mpy.stat("/nonexistent_path_12345")
        self.assertIsNone(result)

    def test_05d_tree_on_file(self):
        """Test tree on file (not directory) returns file info"""
        path, size, children = self.mpy.tree(self.TEST_FILE)
        self.assertEqual(path, self.TEST_FILE)
        self.assertEqual(size, len(self.TEST_CONTENT))
        self.assertIsNone(children)  # file has no children

    def test_06_delete_file(self):
        """Test deleting file"""
        self.mpy.delete(self.TEST_FILE)
        result = self.mpy.stat(self.TEST_FILE)
        self.assertIsNone(result)

    def test_07_delete_dir(self):
        """Test deleting directory"""
        self.mpy.delete(self.TEST_DIR)
        result = self.mpy.stat(self.TEST_DIR)
        self.assertIsNone(result)


@requires_device
class TestExec(unittest.TestCase):
    """Test code execution on device"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()

    def test_exec_simple(self):
        """Test simple code execution"""
        result = self.mpy.comm.exec("x = 1 + 1")
        self.assertEqual(result, b"")

    def test_exec_eval(self):
        """Test expression evaluation"""
        result = self.mpy.comm.exec_eval("1 + 1")
        self.assertEqual(result, 2)

    def test_exec_eval_string(self):
        """Test string evaluation"""
        result = self.mpy.comm.exec_eval("repr('hello ' + 'world')")
        self.assertEqual(result, "hello world")

    def test_exec_eval_list(self):
        """Test list evaluation"""
        result = self.mpy.comm.exec_eval("[1, 2, 3]")
        self.assertEqual(result, [1, 2, 3])

    def test_exec_import(self):
        """Test importing modules"""
        self.mpy.comm.exec("import sys")
        result = self.mpy.comm.exec_eval("repr(sys.platform)")
        self.assertIsInstance(result, str)


@requires_device
class TestReplRecovery(unittest.TestCase):
    """Test REPL state recovery after disconnect"""

    def test_recovery_from_raw_repl(self):
        """Test that we can reconnect when device was left in raw REPL mode"""
        from mpytool import ConnSerial, Mpy

        # First connection - enter raw REPL and disconnect without exiting
        conn1 = ConnSerial(port=DEVICE_PORT)
        mpy1 = Mpy(conn1)
        mpy1.comm.enter_raw_repl()
        self.assertTrue(mpy1.comm._repl_mode)

        # Close connection WITHOUT exiting raw REPL
        # This simulates a crash or unexpected disconnect
        del mpy1
        del conn1

        # Second connection - device is still in raw REPL mode
        conn2 = ConnSerial(port=DEVICE_PORT)
        mpy2 = Mpy(conn2)

        # This should recover and work
        # With broken implementation, this will fail/timeout
        try:
            result = mpy2.comm.exec_eval("1 + 1")
            self.assertEqual(result, 2)
        finally:
            mpy2.comm.exit_raw_repl()


@requires_device
class TestDeviceInfo(unittest.TestCase):
    """Test getting device information"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()

    def test_get_platform(self):
        """Test getting platform info"""
        self.mpy.comm.exec("import sys")
        platform = self.mpy.comm.exec_eval("repr(sys.platform)")
        self.assertIsInstance(platform, str)

    def test_get_version(self):
        """Test getting MicroPython version"""
        self.mpy.comm.exec("import sys")
        version = self.mpy.comm.exec_eval("repr(sys.version)")
        self.assertIn("MicroPython", version)

    def test_get_mem_free(self):
        """Test getting free memory"""
        self.mpy.comm.exec("import gc")
        mem_free = self.mpy.comm.exec_eval("gc.mem_free()")
        self.assertIsInstance(mem_free, int)
        self.assertGreater(mem_free, 0)


@requires_device
class TestCpCommand(unittest.TestCase):
    """Test cp command for file copying"""

    TEST_DIR = "/_mpytool_cp_test"
    LOCAL_DIR = "/tmp/_mpytool_cp_test"

    @classmethod
    def setUpClass(cls):
        import os
        import shutil
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)
        # Setup local test directory
        if os.path.exists(cls.LOCAL_DIR):
            shutil.rmtree(cls.LOCAL_DIR)
        os.makedirs(cls.LOCAL_DIR)
        # Setup remote test directory
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.mkdir(cls.TEST_DIR)

    @classmethod
    def tearDownClass(cls):
        import os
        import shutil
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        if os.path.exists(cls.LOCAL_DIR):
            shutil.rmtree(cls.LOCAL_DIR)
        cls.mpy.comm.exit_raw_repl()

    def test_01_upload_file(self):
        """Test cp local file to remote"""
        import os
        local_file = os.path.join(self.LOCAL_DIR, "upload.txt")
        with open(local_file, 'w') as f:
            f.write("upload test")
        remote_file = self.TEST_DIR + "/upload.txt"
        self.tool.cmd_cp(local_file, ':' + remote_file)
        content = self.mpy.get(remote_file)
        self.assertEqual(content, b"upload test")

    def test_02_download_file(self):
        """Test cp remote file to local"""
        import os
        remote_file = self.TEST_DIR + "/download.txt"
        self.mpy.put(b"download test", remote_file)
        local_file = os.path.join(self.LOCAL_DIR, "download.txt")
        self.tool.cmd_cp(':' + remote_file, local_file)
        with open(local_file, 'r') as f:
            content = f.read()
        self.assertEqual(content, "download test")

    def test_03_upload_to_dir(self):
        """Test cp local file to remote directory"""
        import os
        local_file = os.path.join(self.LOCAL_DIR, "todir.txt")
        with open(local_file, 'w') as f:
            f.write("to dir test")
        self.tool.cmd_cp(local_file, ':' + self.TEST_DIR + '/')
        content = self.mpy.get(self.TEST_DIR + "/todir.txt")
        self.assertEqual(content, b"to dir test")

    def test_04_download_to_dir(self):
        """Test cp remote file to local directory"""
        import os
        remote_file = self.TEST_DIR + "/fromdir.txt"
        self.mpy.put(b"from dir test", remote_file)
        self.tool.cmd_cp(':' + remote_file, self.LOCAL_DIR + '/')
        local_file = os.path.join(self.LOCAL_DIR, "fromdir.txt")
        with open(local_file, 'r') as f:
            content = f.read()
        self.assertEqual(content, "from dir test")

    def test_05_upload_multiple(self):
        """Test cp multiple local files to remote directory"""
        import os
        file1 = os.path.join(self.LOCAL_DIR, "multi1.txt")
        file2 = os.path.join(self.LOCAL_DIR, "multi2.txt")
        with open(file1, 'w') as f:
            f.write("multi1")
        with open(file2, 'w') as f:
            f.write("multi2")
        subdir = self.TEST_DIR + "/multi"
        self.tool.cmd_cp(file1, file2, ':' + subdir + '/')
        self.assertEqual(self.mpy.get(subdir + "/multi1.txt"), b"multi1")
        self.assertEqual(self.mpy.get(subdir + "/multi2.txt"), b"multi2")

    def test_06_remote_to_remote(self):
        """Test cp remote file to remote"""
        src = self.TEST_DIR + "/src.txt"
        dst = self.TEST_DIR + "/dst.txt"
        self.mpy.put(b"remote copy", src)
        self.tool.cmd_cp(':' + src, ':' + dst)
        self.assertEqual(self.mpy.get(dst), b"remote copy")

    def test_07_upload_directory(self):
        """Test cp local directory to remote"""
        import os
        subdir = os.path.join(self.LOCAL_DIR, "subdir")
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, "file.txt"), 'w') as f:
            f.write("subdir file")
        self.tool.cmd_cp(subdir, ':' + self.TEST_DIR + '/')
        content = self.mpy.get(self.TEST_DIR + "/subdir/file.txt")
        self.assertEqual(content, b"subdir file")


@requires_device
class TestMvCommand(unittest.TestCase):
    """Test mv command for moving/renaming files on device"""

    TEST_DIR = "/_mpytool_mv_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.mkdir(cls.TEST_DIR)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()

    def test_01_rename_file(self):
        """Test mv rename file"""
        src = self.TEST_DIR + "/old.txt"
        dst = self.TEST_DIR + "/new.txt"
        self.mpy.put(b"rename test", src)
        self.tool.cmd_mv(':' + src, ':' + dst)
        self.assertIsNone(self.mpy.stat(src))
        self.assertEqual(self.mpy.get(dst), b"rename test")

    def test_02_move_to_dir(self):
        """Test mv file to directory"""
        src = self.TEST_DIR + "/moveme.txt"
        dst_dir = self.TEST_DIR + "/subdir"
        self.mpy.put(b"move test", src)
        self.tool.cmd_mv(':' + src, ':' + dst_dir + '/')
        self.assertIsNone(self.mpy.stat(src))
        self.assertEqual(self.mpy.get(dst_dir + "/moveme.txt"), b"move test")

    def test_03_move_multiple(self):
        """Test mv multiple files to directory"""
        src1 = self.TEST_DIR + "/multi1.txt"
        src2 = self.TEST_DIR + "/multi2.txt"
        dst_dir = self.TEST_DIR + "/multidir"
        self.mpy.put(b"multi1", src1)
        self.mpy.put(b"multi2", src2)
        self.tool.cmd_mv(':' + src1, ':' + src2, ':' + dst_dir + '/')
        self.assertIsNone(self.mpy.stat(src1))
        self.assertIsNone(self.mpy.stat(src2))
        self.assertEqual(self.mpy.get(dst_dir + "/multi1.txt"), b"multi1")
        self.assertEqual(self.mpy.get(dst_dir + "/multi2.txt"), b"multi2")


@requires_device
class TestDeleteCommand(unittest.TestCase):
    """Test delete command with trailing / behavior"""

    TEST_DIR = "/_mpytool_del_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()

    def test_01_delete_file(self):
        """Test delete single file"""
        path = self.TEST_DIR + "/file.txt"
        self.mpy.mkdir(self.TEST_DIR)
        self.mpy.put(b"test", path)
        self.assertIsNotNone(self.mpy.stat(path))
        self.tool.cmd_delete(path)
        self.assertIsNone(self.mpy.stat(path))

    def test_02_delete_dir(self):
        """Test delete directory and contents"""
        subdir = self.TEST_DIR + "/subdir"
        self.mpy.mkdir(subdir)
        self.mpy.put(b"test", subdir + "/file.txt")
        self.assertIsNotNone(self.mpy.stat(subdir))
        self.tool.cmd_delete(subdir)
        self.assertIsNone(self.mpy.stat(subdir))

    def test_03_delete_contents_only(self):
        """Test delete with trailing / keeps directory"""
        subdir = self.TEST_DIR + "/keepme"
        self.mpy.mkdir(subdir)
        self.mpy.put(b"file1", subdir + "/a.txt")
        self.mpy.put(b"file2", subdir + "/b.txt")
        # Delete contents only
        self.tool.cmd_delete(subdir + '/')
        # Directory should still exist
        self.assertEqual(self.mpy.stat(subdir), -1)
        # But be empty
        self.assertEqual(self.mpy.ls(subdir), [])

    def test_04_delete_nested_contents(self):
        """Test delete contents with nested directories"""
        subdir = self.TEST_DIR + "/nested"
        self.mpy.mkdir(subdir + "/deep")
        self.mpy.put(b"file1", subdir + "/file.txt")
        self.mpy.put(b"file2", subdir + "/deep/file.txt")
        # Delete contents only
        self.tool.cmd_delete(subdir + '/')
        # Directory should still exist but be empty
        self.assertEqual(self.mpy.stat(subdir), -1)
        self.assertEqual(self.mpy.ls(subdir), [])


if __name__ == "__main__":
    unittest.main()
