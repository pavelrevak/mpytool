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
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

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
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        # Cleanup test directory
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

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
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

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
        conn1 = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        mpy1 = Mpy(conn1)
        mpy1.comm.enter_raw_repl()
        self.assertTrue(mpy1.comm._repl_mode)

        # Close connection WITHOUT exiting raw REPL
        # This simulates a crash or unexpected disconnect
        del mpy1
        conn1.close()

        # Second connection - device is still in raw REPL mode
        conn2 = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        mpy2 = Mpy(conn2)

        # This should recover and work
        # With broken implementation, this will fail/timeout
        try:
            result = mpy2.comm.exec_eval("1 + 1")
            self.assertEqual(result, 2)
        finally:
            mpy2.comm.exit_raw_repl()
            conn2.close()


@requires_device
class TestDeviceInfo(unittest.TestCase):
    """Test getting device information"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

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
class TestCwdOperations(unittest.TestCase):
    """Test current working directory operations (pwd, cd)"""

    TEST_DIR = "/_mpytool_cwd_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)
        cls.tool._mpy = cls.mpy  # share Mpy instance
        # Setup test directory
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.mkdir(cls.TEST_DIR)
        cls.mpy.mkdir(cls.TEST_DIR + "/subdir")

    @classmethod
    def tearDownClass(cls):
        # Return to root before cleanup
        try:
            cls.mpy.chdir('/')
        except Exception:
            pass
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def setUp(self):
        # Always start from root
        self.mpy.chdir('/')

    def test_01_getcwd_root(self):
        """Test getcwd returns root directory"""
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, '/')

    def test_02_chdir_absolute(self):
        """Test chdir to absolute path"""
        self.mpy.chdir(self.TEST_DIR)
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, self.TEST_DIR)

    def test_03_chdir_relative(self):
        """Test chdir to relative path"""
        self.mpy.chdir(self.TEST_DIR)
        self.mpy.chdir('subdir')
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, self.TEST_DIR + '/subdir')

    def test_04_chdir_parent(self):
        """Test chdir to parent directory"""
        self.mpy.chdir(self.TEST_DIR + '/subdir')
        self.mpy.chdir('..')
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, self.TEST_DIR)

    def test_05_chdir_to_root(self):
        """Test chdir back to root"""
        self.mpy.chdir(self.TEST_DIR)
        self.mpy.chdir('/')
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, '/')

    def test_06_chdir_nonexistent_raises(self):
        """Test chdir to nonexistent directory raises DirNotFound"""
        from mpytool.mpy import DirNotFound
        with self.assertRaises(DirNotFound):
            self.mpy.chdir('/nonexistent_dir_12345')

    def test_07_chdir_to_file_raises(self):
        """Test chdir to file raises error"""
        from mpytool.mpy import DirNotFound
        # Create a file
        test_file = self.TEST_DIR + '/testfile.txt'
        self.mpy.put(b'test', test_file)
        with self.assertRaises(DirNotFound):
            self.mpy.chdir(test_file)
        self.mpy.delete(test_file)

    def test_08_cmd_pwd(self):
        """Test pwd command via process_commands"""
        import io
        import sys
        self.mpy.chdir(self.TEST_DIR)
        # Capture stdout
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            self.tool.process_commands(['pwd'])
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue().strip()
        self.assertEqual(output, self.TEST_DIR)

    def test_09_cmd_cd(self):
        """Test cd command via process_commands"""
        self.tool.process_commands(['cd', ':' + self.TEST_DIR])
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, self.TEST_DIR)

    def test_10_cmd_cd_relative(self):
        """Test cd command with relative path"""
        self.tool.process_commands(['cd', ':' + self.TEST_DIR])
        self.tool.process_commands(['cd', ':subdir'])
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, self.TEST_DIR + '/subdir')

    def test_11_cmd_cd_parent(self):
        """Test cd command to parent"""
        self.tool.process_commands(['cd', ':' + self.TEST_DIR + '/subdir'])
        self.tool.process_commands(['cd', ':..'])
        cwd = self.mpy.getcwd()
        self.assertEqual(cwd, self.TEST_DIR)

    def test_12_ls_respects_cwd(self):
        """Test that ls respects current working directory"""
        # Create file in test dir
        test_file = self.TEST_DIR + '/cwd_test.txt'
        self.mpy.put(b'test', test_file)
        # Change to test dir and ls
        self.mpy.chdir(self.TEST_DIR)
        result = self.mpy.ls('')
        names = [name for name, size in result]
        self.assertIn('cwd_test.txt', names)
        self.mpy.delete(test_file)

    def test_13_put_respects_cwd(self):
        """Test that put respects current working directory"""
        self.mpy.chdir(self.TEST_DIR)
        self.mpy.put(b'cwd put test', 'cwd_put.txt')
        # Verify file was created in TEST_DIR
        content = self.mpy.get(self.TEST_DIR + '/cwd_put.txt')
        self.assertEqual(content, b'cwd put test')
        self.mpy.delete(self.TEST_DIR + '/cwd_put.txt')


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
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
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
        cls.conn.close()

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
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
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
        cls.conn.close()

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
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def test_01_delete_file(self):
        """Test rm single file"""
        path = self.TEST_DIR + "/file.txt"
        self.mpy.mkdir(self.TEST_DIR)
        self.mpy.put(b"test", path)
        self.assertIsNotNone(self.mpy.stat(path))
        self.tool.cmd_rm(':' + path)
        self.assertIsNone(self.mpy.stat(path))

    def test_02_delete_dir(self):
        """Test rm directory and contents"""
        subdir = self.TEST_DIR + "/subdir"
        self.mpy.mkdir(subdir)
        self.mpy.put(b"test", subdir + "/file.txt")
        self.assertIsNotNone(self.mpy.stat(subdir))
        self.tool.cmd_rm(':' + subdir)
        self.assertIsNone(self.mpy.stat(subdir))

    def test_03_delete_contents_only(self):
        """Test rm with trailing / keeps directory"""
        subdir = self.TEST_DIR + "/keepme"
        self.mpy.mkdir(subdir)
        self.mpy.put(b"file1", subdir + "/a.txt")
        self.mpy.put(b"file2", subdir + "/b.txt")
        # Delete contents only
        self.tool.cmd_rm(':' + subdir + '/')
        # Directory should still exist
        self.assertEqual(self.mpy.stat(subdir), -1)
        # But be empty
        self.assertEqual(self.mpy.ls(subdir), [])

    def test_04_delete_nested_contents(self):
        """Test rm contents with nested directories"""
        subdir = self.TEST_DIR + "/nested"
        self.mpy.mkdir(subdir + "/deep")
        self.mpy.put(b"file1", subdir + "/file.txt")
        self.mpy.put(b"file2", subdir + "/deep/file.txt")
        # Delete contents only
        self.tool.cmd_rm(':' + subdir + '/')
        # Directory should still exist but be empty
        self.assertEqual(self.mpy.stat(subdir), -1)
        self.assertEqual(self.mpy.ls(subdir), [])


@requires_device
class TestSkipUnchangedFiles(unittest.TestCase):
    """Test skip unchanged files feature"""

    TEST_DIR = "/_mpytool_skip_test"
    TEST_FILE = "/_mpytool_skip_test/test.txt"
    TEST_CONTENT = b"test content for hash check"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn, force=False)
        cls.tool_force = MpyTool(cls.conn, force=True)
        # Setup test directory
        cls.mpy.mkdir(cls.TEST_DIR)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def test_01_hashfile(self):
        """Test hashfile method returns correct SHA256"""
        import hashlib
        self.mpy.put(self.TEST_CONTENT, self.TEST_FILE)
        remote_hash = self.mpy.hashfile(self.TEST_FILE)
        local_hash = hashlib.sha256(self.TEST_CONTENT).digest()
        self.assertEqual(remote_hash, local_hash)

    def test_02_file_needs_update_same(self):
        """Test _file_needs_update returns False for identical file"""
        self.mpy.put(self.TEST_CONTENT, self.TEST_FILE)
        needs_update = self.tool._file_needs_update(self.TEST_CONTENT, self.TEST_FILE)
        self.assertFalse(needs_update)

    def test_03_file_needs_update_different_size(self):
        """Test _file_needs_update returns True for different size"""
        self.mpy.put(self.TEST_CONTENT, self.TEST_FILE)
        different_content = b"different"
        needs_update = self.tool._file_needs_update(different_content, self.TEST_FILE)
        self.assertTrue(needs_update)

    def test_04_file_needs_update_different_content(self):
        """Test _file_needs_update returns True for same size but different content"""
        self.mpy.put(self.TEST_CONTENT, self.TEST_FILE)
        # Same length, different content
        different_content = b"X" * len(self.TEST_CONTENT)
        needs_update = self.tool._file_needs_update(different_content, self.TEST_FILE)
        self.assertTrue(needs_update)

    def test_05_file_needs_update_nonexistent(self):
        """Test _file_needs_update returns True for nonexistent file"""
        needs_update = self.tool._file_needs_update(b"test", self.TEST_DIR + "/nonexistent.txt")
        self.assertTrue(needs_update)

    def test_06_force_flag(self):
        """Test force flag bypasses check"""
        self.mpy.put(self.TEST_CONTENT, self.TEST_FILE)
        # With force=True, should always return True
        needs_update = self.tool_force._file_needs_update(self.TEST_CONTENT, self.TEST_FILE)
        self.assertTrue(needs_update)


@requires_device
class TestEncodingAndCompression(unittest.TestCase):
    """Test encoding selection and compression"""

    TEST_DIR = "/_mpytool_enc_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.mpy.mkdir(cls.TEST_DIR)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def test_01_text_upload_uses_raw(self):
        """Test that text files use raw encoding"""
        data = b"Hello World, this is plain text!"
        path = self.TEST_DIR + "/text.txt"
        encodings, wire_bytes = self.mpy.put(data, path)
        self.assertIn('raw', encodings)
        # Verify file content
        self.assertEqual(self.mpy.get(path), data)

    def test_02_binary_upload_uses_base64_or_compressed(self):
        """Test that binary files use base64 or compressed encoding"""
        data = bytes(range(256)) * 4  # 1KB of all byte values
        path = self.TEST_DIR + "/binary.bin"
        encodings, wire_bytes = self.mpy.put(data, path)
        # Binary data uses base64 or compressed (if deflate available)
        self.assertTrue('base64' in encodings or 'compressed' in encodings)
        # Verify file content
        self.assertEqual(self.mpy.get(path), data)

    def test_03_compression_reduces_wire_bytes(self):
        """Test that compression reduces wire bytes for compressible data"""
        data = b"A" * 2000  # Highly compressible
        path = self.TEST_DIR + "/compress.txt"
        encodings, wire_bytes = self.mpy.put(data, path, compress=True)
        self.assertIn('compressed', encodings)
        # Wire bytes should be much less than data size
        self.assertLess(wire_bytes, len(data))
        # Verify file content
        self.assertEqual(self.mpy.get(path), data)

    def test_04_compression_not_used_for_incompressible(self):
        """Test that compression is not used when data doesn't compress well"""
        import os
        data = os.urandom(500)  # Random data doesn't compress
        path = self.TEST_DIR + "/random.bin"
        encodings, wire_bytes = self.mpy.put(data, path, compress=True)
        # Should use base64 instead of compressed
        self.assertNotIn('compressed', encodings)
        # Verify file content
        self.assertEqual(self.mpy.get(path), data)

    def test_05_auto_chunk_size_detection(self):
        """Test that chunk size is auto-detected based on RAM"""
        # Reset cache to force re-detection
        from mpytool.mpy import Mpy as MpyClass
        MpyClass._CHUNK_AUTO_DETECTED = None
        # Upload triggers detection
        data = b"test"
        path = self.TEST_DIR + "/chunk_test.txt"
        self.mpy.put(data, path)
        # Chunk size should be detected and cached
        self.assertIsNotNone(MpyClass._CHUNK_AUTO_DETECTED)
        self.assertIn(MpyClass._CHUNK_AUTO_DETECTED, [512, 1024, 2048, 4096, 8192, 16384, 32768])


@requires_device
class TestCpWithFlags(unittest.TestCase):
    """Test cp command with -f and -z flags"""

    TEST_DIR = "/_mpytool_cpflags_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)
        cls.tool_compress = MpyTool(cls.conn, compress=True)
        cls.tool_force = MpyTool(cls.conn, force=True)
        cls.mpy.mkdir(cls.TEST_DIR)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def test_01_cp_with_combined_flags(self):
        """Test cp -fz with combined flags"""
        import tempfile
        import os
        # Create temp file
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False) as f:
            f.write(b"A" * 1000)
            temp_path = f.name
        try:
            # First upload
            self.tool.cmd_cp(temp_path, ':' + self.TEST_DIR + '/')
            # Second upload with -fz should force and compress
            self.tool.cmd_cp('-fz', temp_path, ':' + self.TEST_DIR + '/')
            # Verify file exists
            basename = os.path.basename(temp_path)
            content = self.mpy.get(self.TEST_DIR + '/' + basename)
            self.assertEqual(content, b"A" * 1000)
        finally:
            os.unlink(temp_path)

    def test_02_global_compress_flag(self):
        """Test global compress flag from MpyTool constructor"""
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False) as f:
            f.write(b"B" * 1000)
            temp_path = f.name
        try:
            self.tool_compress.cmd_cp('-f', temp_path, ':' + self.TEST_DIR + '/')
            basename = os.path.basename(temp_path)
            content = self.mpy.get(self.TEST_DIR + '/' + basename)
            self.assertEqual(content, b"B" * 1000)
        finally:
            os.unlink(temp_path)


@requires_device
class TestSleepCommand(unittest.TestCase):
    """Test sleep command"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)
        cls.tool._mpy = cls.mpy  # share Mpy instance

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def test_sleep_delays_execution(self):
        """Test that sleep actually delays execution"""
        import time
        start = time.time()
        self.tool.process_commands(['sleep', '0.5'])
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 0.4)
        self.assertLess(elapsed, 1.0)


@requires_device
class TestSpecialCharacterFilenames(unittest.TestCase):
    """Test file operations with special characters in filenames

    Based on mpremote issues:
    - #18657: apostrophe in filename
    - #18658: equals sign in filename
    - #18656, #18659, #18643: unicode handling
    """

    TEST_DIR = "/_mpytool_special_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
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
        cls.conn.close()

    def test_01_filename_with_equals_sign(self):
        """Test file with equals sign in name (mpremote #18658)"""
        path = self.TEST_DIR + "/file=value.txt"
        content = b"equals sign test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_02_filename_with_apostrophe(self):
        """Test file with apostrophe in name (mpremote #18657)"""
        path = self.TEST_DIR + "/it's_a_file.txt"
        content = b"apostrophe test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_03_filename_with_spaces(self):
        """Test file with spaces in name"""
        path = self.TEST_DIR + "/file with spaces.txt"
        content = b"spaces test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_04_filename_with_multiple_special_chars(self):
        """Test file with multiple special characters"""
        path = self.TEST_DIR + "/it's a=test file.txt"
        content = b"multiple special chars test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_05_directory_with_special_chars(self):
        """Test directory with special characters"""
        dir_path = self.TEST_DIR + "/dir's=test"
        file_path = dir_path + "/file.txt"
        content = b"nested test"
        self.mpy.mkdir(dir_path)
        self.mpy.put(content, file_path)
        self.assertEqual(self.mpy.get(file_path), content)
        self.mpy.delete(dir_path)

    def test_06_ls_with_special_chars(self):
        """Test ls on files with special characters"""
        path = self.TEST_DIR + "/list=test's.txt"
        self.mpy.put(b"test", path)
        result = self.mpy.ls(self.TEST_DIR)
        names = [name for name, size in result]
        self.assertIn("list=test's.txt", names)
        self.mpy.delete(path)

    def test_07_tree_with_special_chars(self):
        """Test tree on directory with special characters"""
        dir_path = self.TEST_DIR + "/tree's=dir"
        self.mpy.mkdir(dir_path)
        self.mpy.put(b"test", dir_path + "/file.txt")
        path, size, children = self.mpy.tree(dir_path)
        self.assertEqual(path, dir_path)
        self.assertIsInstance(children, list)
        self.mpy.delete(dir_path)


@requires_device
class TestUnicodeFilenames(unittest.TestCase):
    """Test file operations with unicode filenames

    Based on mpremote issues:
    - #18656: UnicodeEncodeError on Windows
    - #18659: Unicode content hangs
    - #18643: UnicodeError on specific hardware
    """

    TEST_DIR = "/_mpytool_unicode_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
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
        cls.conn.close()

    def test_01_czech_filename(self):
        """Test file with Czech characters in name"""
        path = self.TEST_DIR + "/súbor.txt"
        content = b"czech filename test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_02_german_filename(self):
        """Test file with German umlauts in name"""
        path = self.TEST_DIR + "/größe.txt"
        content = b"german filename test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_03_chinese_filename(self):
        """Test file with Chinese characters in name"""
        path = self.TEST_DIR + "/文件.txt"
        content = b"chinese filename test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_04_japanese_filename(self):
        """Test file with Japanese characters in name"""
        path = self.TEST_DIR + "/ファイル.txt"
        content = b"japanese filename test"
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_05_unicode_content(self):
        """Test file with unicode content"""
        path = self.TEST_DIR + "/unicode_content.txt"
        content = "Příliš žluťoučký kůň úpěl ďábelské ódy. 日本語テスト".encode('utf-8')
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_06_unicode_directory(self):
        """Test directory with unicode name"""
        dir_path = self.TEST_DIR + "/složka"
        file_path = dir_path + "/soubor.txt"
        content = b"nested unicode test"
        self.mpy.mkdir(dir_path)
        self.mpy.put(content, file_path)
        self.assertEqual(self.mpy.get(file_path), content)
        self.mpy.delete(dir_path)

    def test_07_ls_unicode_files(self):
        """Test ls on files with unicode names"""
        path = self.TEST_DIR + "/čeština.txt"
        self.mpy.put(b"test", path)
        result = self.mpy.ls(self.TEST_DIR)
        names = [name for name, size in result]
        self.assertIn("čeština.txt", names)
        self.mpy.delete(path)


@requires_device
class TestCpSpecialFilenames(unittest.TestCase):
    """Test cp command with special character filenames

    Based on mpremote issues #18657, #18658
    """

    TEST_DIR = "/_mpytool_cpspecial_test"
    LOCAL_DIR = "/tmp/_mpytool_cpspecial_test"

    @classmethod
    def setUpClass(cls):
        import os
        import shutil
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
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
        cls.conn.close()

    def test_01_upload_file_with_equals(self):
        """Test cp upload file with equals sign (mpremote #18658)"""
        import os
        local_file = os.path.join(self.LOCAL_DIR, "test=value.txt")
        with open(local_file, 'w') as f:
            f.write("equals upload test")
        remote_file = self.TEST_DIR + "/test=value.txt"
        self.tool.cmd_cp(local_file, ':' + remote_file)
        content = self.mpy.get(remote_file)
        self.assertEqual(content, b"equals upload test")

    def test_02_download_file_with_equals(self):
        """Test cp download file with equals sign (mpremote #18658)"""
        import os
        remote_file = self.TEST_DIR + "/download=test.txt"
        self.mpy.put(b"equals download test", remote_file)
        local_file = os.path.join(self.LOCAL_DIR, "download=test.txt")
        self.tool.cmd_cp(':' + remote_file, local_file)
        with open(local_file, 'r') as f:
            content = f.read()
        self.assertEqual(content, "equals download test")

    def test_03_upload_file_with_apostrophe(self):
        """Test cp upload file with apostrophe (mpremote #18657)"""
        import os
        local_file = os.path.join(self.LOCAL_DIR, "it's_test.txt")
        with open(local_file, 'w') as f:
            f.write("apostrophe upload test")
        remote_file = self.TEST_DIR + "/it's_test.txt"
        self.tool.cmd_cp(local_file, ':' + remote_file)
        content = self.mpy.get(remote_file)
        self.assertEqual(content, b"apostrophe upload test")

    def test_04_download_file_with_apostrophe(self):
        """Test cp download file with apostrophe (mpremote #18657)"""
        import os
        remote_file = self.TEST_DIR + "/down's_test.txt"
        self.mpy.put(b"apostrophe download test", remote_file)
        local_file = os.path.join(self.LOCAL_DIR, "down's_test.txt")
        self.tool.cmd_cp(':' + remote_file, local_file)
        with open(local_file, 'r') as f:
            content = f.read()
        self.assertEqual(content, "apostrophe download test")

    def test_05_upload_file_with_unicode(self):
        """Test cp upload file with unicode name"""
        import os
        local_file = os.path.join(self.LOCAL_DIR, "súbor.txt")
        with open(local_file, 'w') as f:
            f.write("unicode upload test")
        remote_file = self.TEST_DIR + "/súbor.txt"
        self.tool.cmd_cp(local_file, ':' + remote_file)
        content = self.mpy.get(remote_file)
        self.assertEqual(content, b"unicode upload test")

    def test_06_download_file_with_unicode(self):
        """Test cp download file with unicode name"""
        import os
        remote_file = self.TEST_DIR + "/stáhni.txt"
        self.mpy.put(b"unicode download test", remote_file)
        local_file = os.path.join(self.LOCAL_DIR, "stáhni.txt")
        self.tool.cmd_cp(':' + remote_file, local_file)
        with open(local_file, 'r') as f:
            content = f.read()
        self.assertEqual(content, "unicode download test")


@requires_device
class TestErrorMessages(unittest.TestCase):
    """Test error messages for various failure cases

    Based on mpremote issue #17267 - misleading error messages
    """

    TEST_DIR = "/_mpytool_error_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        from mpytool.mpy import PathNotFound, FileNotFound, DirNotFound
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)
        cls.PathNotFound = PathNotFound
        cls.FileNotFound = FileNotFound
        cls.DirNotFound = DirNotFound
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
        cls.conn.close()

    def test_01_get_nonexistent_file(self):
        """Test get on nonexistent file raises FileNotFound"""
        with self.assertRaises(self.FileNotFound):
            self.mpy.get(self.TEST_DIR + "/nonexistent.txt")

    def test_02_delete_nonexistent_path(self):
        """Test delete on nonexistent path raises PathNotFound"""
        with self.assertRaises(self.PathNotFound):
            self.mpy.delete(self.TEST_DIR + "/nonexistent.txt")

    def test_03_ls_nonexistent_dir(self):
        """Test ls on nonexistent directory raises DirNotFound"""
        with self.assertRaises(self.DirNotFound):
            self.mpy.ls(self.TEST_DIR + "/nonexistent_dir")

    def test_04_stat_returns_none_for_nonexistent(self):
        """Test stat returns None for nonexistent path (not error)"""
        result = self.mpy.stat(self.TEST_DIR + "/definitely_nonexistent.txt")
        self.assertIsNone(result)

    def test_05_tree_on_nonexistent_raises(self):
        """Test tree on nonexistent path raises PathNotFound"""
        with self.assertRaises(self.PathNotFound):
            self.mpy.tree(self.TEST_DIR + "/nonexistent_path")


@requires_device
class TestLargeFileTransfer(unittest.TestCase):
    """Test large file transfers with chunking

    Based on mpremote issues about slow/hanging transfers
    """

    TEST_DIR = "/_mpytool_large_test"

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
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
        cls.conn.close()

    def test_01_upload_10kb_file(self):
        """Test upload of 10KB file"""
        path = self.TEST_DIR + "/10kb.bin"
        content = bytes(range(256)) * 40  # 10KB
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_02_upload_50kb_file(self):
        """Test upload of 50KB file"""
        path = self.TEST_DIR + "/50kb.bin"
        content = bytes(range(256)) * 200  # ~50KB
        self.mpy.put(content, path)
        self.assertEqual(self.mpy.get(path), content)
        self.mpy.delete(path)

    def test_03_upload_compressible_file(self):
        """Test upload of compressible file"""
        path = self.TEST_DIR + "/compress.txt"
        content = b"A" * 20000  # 20KB, highly compressible
        encodings, wire_bytes = self.mpy.put(content, path, compress=True)
        self.assertEqual(self.mpy.get(path), content)
        # Should use compression
        self.assertIn('compressed', encodings)
        # Wire bytes should be much less
        self.assertLess(wire_bytes, len(content) // 2)
        self.mpy.delete(path)


@requires_device
class TestPartitions(unittest.TestCase):
    """Test partition operations (ESP32 only)"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        # Check if ESP32
        try:
            platform = cls.mpy.comm.exec_eval("repr(__import__('sys').platform)")
            cls.is_esp32 = 'esp32' in platform.lower()
        except Exception:
            cls.is_esp32 = False

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def test_01_partitions_list(self):
        """Test listing partitions on ESP32"""
        if not self.is_esp32:
            self.skipTest("Not an ESP32 device")
        info = self.mpy.partitions()
        self.assertIn('partitions', info)
        self.assertIsInstance(info['partitions'], list)
        self.assertGreater(len(info['partitions']), 0)
        # Check partition structure
        part = info['partitions'][0]
        self.assertIn('label', part)
        self.assertIn('size', part)
        self.assertIn('offset', part)

    def test_02_partition_read_nvs(self):
        """Test reading NVS partition (small, safe to read)"""
        if not self.is_esp32:
            self.skipTest("Not an ESP32 device")
        # Find nvs partition
        info = self.mpy.partitions()
        nvs = None
        for p in info['partitions']:
            if p['label'] == 'nvs':
                nvs = p
                break
        if not nvs:
            self.skipTest("No nvs partition found")
        # Read nvs partition
        data = self.mpy.flash_read(label='nvs')
        self.assertIsInstance(data, bytes)
        self.assertEqual(len(data), nvs['size'])


@requires_device
class TestFlashRP2(unittest.TestCase):
    """Test flash operations (RP2 only)"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        # Check if RP2
        try:
            platform = cls.mpy.comm.exec_eval("repr(__import__('sys').platform)")
            cls.is_rp2 = platform == 'rp2'
        except Exception:
            cls.is_rp2 = False

    @classmethod
    def tearDownClass(cls):
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def test_01_flash_info(self):
        """Test getting flash info on RP2"""
        if not self.is_rp2:
            self.skipTest("Not an RP2 device")
        info = self.mpy.flash_info()
        self.assertIn('size', info)
        self.assertIn('block_size', info)
        self.assertIn('block_count', info)
        self.assertIn('filesystem', info)
        self.assertIn('magic', info)
        # Check values
        self.assertIsInstance(info['size'], int)
        self.assertIsInstance(info['block_size'], int)
        self.assertIsInstance(info['block_count'], int)
        self.assertGreater(info['size'], 0)
        self.assertGreater(info['block_size'], 0)
        self.assertEqual(info['size'], info['block_size'] * info['block_count'])

    def test_02_flash_info_filesystem(self):
        """Test filesystem detection on RP2"""
        if not self.is_rp2:
            self.skipTest("Not an RP2 device")
        info = self.mpy.flash_info()
        # Filesystem should be detected (littlefs2 is default on RP2)
        self.assertIn(info['filesystem'], ('littlefs2', 'fat', 'unknown'))
        # Magic should be 16 bytes
        self.assertIsInstance(info['magic'], bytes)
        self.assertEqual(len(info['magic']), 16)

    def test_03_flash_info_littlefs_magic(self):
        """Test LittleFS magic detection on RP2"""
        if not self.is_rp2:
            self.skipTest("Not an RP2 device")
        info = self.mpy.flash_info()
        if info['filesystem'] == 'littlefs2':
            # Check magic contains "littlefs" at offset 8
            self.assertEqual(info['magic'][8:16], b'littlefs')


@requires_device
class TestRawPasteMode(unittest.TestCase):
    """Test raw-paste mode for code execution"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial
        from mpytool.mpy_comm import MpyComm
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.comm = MpyComm(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.comm.exit_raw_repl()
        cls.conn.close()

    def test_01_raw_paste_simple(self):
        """Test simple code execution via raw-paste"""
        result = self.comm.exec_raw_paste('print(1+1)')
        self.assertEqual(result, bytearray(b'2\r\n'))
        self.assertTrue(self.comm._raw_paste_supported)

    def test_02_raw_paste_multiline(self):
        """Test multiline code via raw-paste"""
        code = 'for i in range(3):\n    print(i)'
        result = self.comm.exec_raw_paste(code)
        self.assertEqual(result, bytearray(b'0\r\n1\r\n2\r\n'))

    def test_03_raw_paste_large_code(self):
        """Test larger code that exceeds window size"""
        # Generate code larger than typical window size (128 bytes)
        lines = [f'x{i} = {i}' for i in range(50)]
        lines.append('print(x49)')
        code = '\n'.join(lines)
        self.assertGreater(len(code), 128)
        result = self.comm.exec_raw_paste(code)
        self.assertEqual(result, bytearray(b'49\r\n'))

    def test_04_raw_paste_syntax_error(self):
        """Test syntax error handling in raw-paste"""
        from mpytool.mpy_comm import CmdError
        with self.assertRaises(CmdError) as ctx:
            self.comm.exec_raw_paste('print(')
        self.assertIn('SyntaxError', ctx.exception.error)

    def test_05_raw_paste_runtime_error(self):
        """Test runtime error handling in raw-paste"""
        from mpytool.mpy_comm import CmdError
        with self.assertRaises(CmdError) as ctx:
            self.comm.exec_raw_paste('1/0')
        self.assertIn('ZeroDivisionError', ctx.exception.error)

    def test_06_try_raw_paste(self):
        """Test try_raw_paste wrapper"""
        result = self.comm.try_raw_paste('print("hello")')
        self.assertEqual(result, bytearray(b'hello\r\n'))

    def test_07_raw_paste_binary_output(self):
        """Test raw-paste with binary output"""
        code = 'import sys; sys.stdout.buffer.write(bytes([0,1,2,255]))'
        result = self.comm.exec_raw_paste(code)
        self.assertEqual(result, bytearray(b'\x00\x01\x02\xff'))

    def test_08_raw_paste_after_regular_exec(self):
        """Test raw-paste works after regular exec"""
        # First use regular exec
        result1 = self.comm.exec('print("regular")')
        self.assertEqual(result1, bytearray(b'regular\r\n'))
        # Then use raw-paste
        result2 = self.comm.exec_raw_paste('print("raw-paste")')
        self.assertEqual(result2, bytearray(b'raw-paste\r\n'))

    def test_09_raw_paste_recovery(self):
        """Test recovery from interrupted raw-paste"""
        # Enter raw-paste mode manually
        self.comm.enter_raw_repl()
        self.conn.write(b'\x05A\x01')  # Enter raw-paste
        self.conn.read_bytes(4)  # R + status + window size
        # Send partial data and abandon
        self.conn.write(b'print(')
        # Reset internal state (simulate reconnect)
        self.comm._repl_mode = None
        self.comm._raw_paste_supported = None
        # Try to recover and execute
        result = self.comm.exec('print("recovered")')
        self.assertEqual(result, bytearray(b'recovered\r\n'))


@requires_device
class TestRunCommand(unittest.TestCase):
    """Test run command"""

    TEST_DIR = "/_mpytool_run_test"

    @classmethod
    def setUpClass(cls):
        import tempfile
        from mpytool import ConnSerial, Mpy
        from mpytool.mpytool import MpyTool
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.tool = MpyTool(cls.conn)
        cls.tool._mpy = cls.mpy
        cls.temp_dir = tempfile.mkdtemp()
        try:
            cls.mpy.mkdir(cls.TEST_DIR)
        except Exception:
            pass

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        try:
            cls.mpy.delete(cls.TEST_DIR)
        except Exception:
            pass
        try:
            cls.mpy.comm.exit_raw_repl()
        except Exception:
            pass
        cls.conn.close()

    def _recover_repl(self):
        """Recover to clean REPL state after run (timeout=0)"""
        import time
        time.sleep(0.5)
        self.mpy.comm._repl_mode = None
        self.mpy.comm.stop_current_operation()

    def test_01_run_creates_file(self):
        """Test run executes script that creates file on device"""
        script_path = os.path.join(self.temp_dir, 'create_file.py')
        with open(script_path, 'w') as f:
            f.write(
                f"with open('{self.TEST_DIR}/result.txt', 'w') as f:\n"
                f"    f.write('hello from run')\n")
        self.tool.process_commands(['run', script_path])
        self._recover_repl()
        content = self.mpy.get(self.TEST_DIR + '/result.txt')
        self.assertEqual(content, b'hello from run')

    def test_02_run_with_monitor(self):
        """Test run file.py -- monitor captures script output"""
        import io
        import signal
        from unittest.mock import patch
        from mpytool.mpytool import _run_commands

        if not hasattr(signal, 'SIGALRM'):
            self.skipTest('SIGALRM not available')

        script_path = os.path.join(self.temp_dir, 'print_lines.py')
        with open(script_path, 'w') as f:
            f.write("for i in range(3):\n    print(f'RUN_TEST_{i}')\n")

        captured = io.StringIO()

        def alarm_handler(signum, frame):
            raise KeyboardInterrupt()

        old_handler = signal.signal(signal.SIGALRM, alarm_handler)
        signal.alarm(3)
        try:
            with patch('sys.stdout', captured):
                _run_commands(
                    self.tool,
                    [['run', script_path], ['monitor']],
                    with_progress=False)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            self._recover_repl()

        output = captured.getvalue()
        self.assertIn('RUN_TEST_0', output)
        self.assertIn('RUN_TEST_1', output)
        self.assertIn('RUN_TEST_2', output)


@requires_device
class TestMount(unittest.TestCase):
    """Test mount command: local directory as VFS on device"""

    LOCAL_DIR = None

    @classmethod
    def setUpClass(cls):
        import tempfile
        from mpytool import ConnSerial, Mpy

        # Create local temp directory with test files
        cls.LOCAL_DIR = tempfile.mkdtemp(prefix='mpytool_mount_test_')
        # Simple text file
        with open(os.path.join(cls.LOCAL_DIR, 'hello.txt'), 'w') as f:
            f.write('Hello from mount test!')
        # Python module
        with open(os.path.join(cls.LOCAL_DIR, 'testmod.py'), 'w') as f:
            f.write('MOUNT_VALUE = 42\ndef double(x):\n    return x * 2\n')
        # Binary file
        with open(os.path.join(cls.LOCAL_DIR, 'data.bin'), 'wb') as f:
            f.write(bytes(range(256)))
        # Subdirectory with file
        subdir = os.path.join(cls.LOCAL_DIR, 'subdir')
        os.makedirs(subdir)
        with open(os.path.join(subdir, 'nested.txt'), 'w') as f:
            f.write('nested content')
        # lib directory with importable module
        libdir = os.path.join(cls.LOCAL_DIR, 'lib')
        os.makedirs(libdir)
        with open(os.path.join(libdir, 'libmod.py'), 'w') as f:
            f.write('LIB_VALUE = 99\n')

        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.handler = cls.mpy.mount(cls.LOCAL_DIR)

    @classmethod
    def tearDownClass(cls):
        import shutil
        try:
            cls.mpy.comm.enter_raw_repl()
            cls.mpy.comm.exec(
                "import uos\ntry:uos.umount('/remote')\nexcept:pass\n"
                "uos.chdir('/')", timeout=3)
        except Exception:
            pass
        try:
            cls.mpy.comm.exit_raw_repl()
        except Exception:
            pass
        cls.conn.close()
        if cls.LOCAL_DIR:
            shutil.rmtree(cls.LOCAL_DIR, ignore_errors=True)

    def test_01_listdir(self):
        """Test os.listdir on mounted VFS"""
        result = self.mpy.comm.exec_eval(
            "repr(__import__('os').listdir('/remote'))")
        self.assertIsInstance(result, list)
        self.assertIn('hello.txt', result)
        self.assertIn('testmod.py', result)
        self.assertIn('data.bin', result)
        self.assertIn('subdir', result)
        self.assertIn('lib', result)

    def test_02_stat_file(self):
        """Test os.stat on file via VFS"""
        result = self.mpy.comm.exec_eval(
            "__import__('os').stat('/remote/hello.txt')")
        self.assertIsInstance(result, tuple)
        # st_size should match local file
        self.assertEqual(result[6], len('Hello from mount test!'))

    def test_03_stat_dir(self):
        """Test os.stat on directory via VFS"""
        result = self.mpy.comm.exec_eval(
            "__import__('os').stat('/remote/subdir')")
        self.assertIsInstance(result, tuple)
        # Directory has S_IFDIR bit (0x4000)
        self.assertTrue(result[0] & 0x4000)

    def test_04_stat_nonexistent(self):
        """Test os.stat on nonexistent path raises OSError"""
        from mpytool.mpy_comm import CmdError
        with self.assertRaises(CmdError) as ctx:
            self.mpy.comm.exec(
                "__import__('os').stat('/remote/nonexistent.xyz')")
        self.assertIn('OSError', ctx.exception.error)

    def test_05_read_text_file(self):
        """Test reading text file via VFS"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/remote/hello.txt').read())")
        self.assertEqual(result, 'Hello from mount test!')

    def test_06_read_binary_file(self):
        """Test reading binary file via VFS"""
        result = self.mpy.comm.exec_eval(
            "open('/remote/data.bin','rb').read()")
        self.assertEqual(result, bytes(range(256)))

    def test_07_read_partial(self):
        """Test partial read of file via VFS"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/remote/hello.txt').read(5))")
        self.assertEqual(result, 'Hello')

    def test_08_readline(self):
        """Test readline on text file via VFS"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/remote/testmod.py').readline())")
        self.assertEqual(result, 'MOUNT_VALUE = 42\n')

    def test_09_listdir_subdir(self):
        """Test os.listdir on subdirectory via VFS"""
        result = self.mpy.comm.exec_eval(
            "repr(__import__('os').listdir('/remote/subdir'))")
        self.assertIn('nested.txt', result)

    def test_10_read_nested_file(self):
        """Test reading file in subdirectory via VFS"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/remote/subdir/nested.txt').read())")
        self.assertEqual(result, 'nested content')

    def test_11_import_module(self):
        """Test importing Python module from VFS"""
        self.mpy.chdir('/remote')
        self.mpy.comm.exec(
            "import sys\n"
            "for k in list(sys.modules):\n"
            " if k in ('testmod','libmod'):del sys.modules[k]\n")
        result = self.mpy.comm.exec_eval(
            "__import__('testmod').MOUNT_VALUE")
        self.assertEqual(result, 42)

    def test_12_import_function(self):
        """Test calling imported function from VFS module"""
        result = self.mpy.comm.exec_eval(
            "__import__('testmod').double(21)")
        self.assertEqual(result, 42)


    def test_14_chdir_and_relative(self):
        """Test chdir to VFS mount and relative path access"""
        self.mpy.comm.exec("__import__('os').chdir('/remote')")
        result = self.mpy.comm.exec_eval(
            "repr(__import__('os').getcwd())")
        self.assertEqual(result, '/remote')
        # Relative listdir
        result = self.mpy.comm.exec_eval(
            "repr(__import__('os').listdir('subdir'))")
        self.assertIn('nested.txt', result)

    def test_15_multiple_open_close(self):
        """Test opening and closing multiple files"""
        code = (
            "f1 = open('/remote/hello.txt')\n"
            "f2 = open('/remote/testmod.py')\n"
            "r1 = f1.read(5)\n"
            "r2 = f2.readline()\n"
            "f1.close()\n"
            "f2.close()\n"
            "print(repr(r1), repr(r2))")
        result = self.mpy.comm.exec(code)
        self.assertIn(b'Hello', result)
        self.assertIn(b'MOUNT_VALUE', result)

    def test_16_file_modified_on_pc(self):
        """Test that file changes on PC are reflected in VFS reads"""
        # Write new content to a file on PC
        dynamic_path = os.path.join(self.LOCAL_DIR, 'dynamic.txt')
        with open(dynamic_path, 'w') as f:
            f.write('version1')
        result = self.mpy.comm.exec_eval(
            "repr(open('/remote/dynamic.txt').read())")
        self.assertEqual(result, 'version1')
        # Modify file on PC
        with open(dynamic_path, 'w') as f:
            f.write('version2')
        result = self.mpy.comm.exec_eval(
            "repr(open('/remote/dynamic.txt').read())")
        self.assertEqual(result, 'version2')

    def test_17_large_file_read(self):
        """Test reading file larger than chunk size via VFS"""
        # Create a 4KB file (likely larger than typical chunk)
        large_path = os.path.join(self.LOCAL_DIR, 'large.txt')
        content = 'L' * 4096
        with open(large_path, 'w') as f:
            f.write(content)
        result = self.mpy.comm.exec_eval(
            "len(open('/remote/large.txt').read())")
        self.assertEqual(result, 4096)

    def test_18_busy_false_by_default(self):
        """Test that conn.busy is False when no VFS request in progress"""
        self.assertFalse(self.mpy.conn.busy)

    def test_19_read_no_data(self):
        """Test conn.read() returns None when no data available"""
        result = self.mpy.conn.read(timeout=0)
        self.assertIsNone(result)

    def test_20_read_with_timeout(self):
        """Test conn.read(timeout) waits and returns None on timeout"""
        import time
        start = time.time()
        result = self.mpy.conn.read(timeout=0.1)
        elapsed = time.time() - start
        self.assertIsNone(result)
        self.assertGreaterEqual(elapsed, 0.05)

    def test_21_read_device_output(self):
        """Test conn.read() returns device output after exec with timeout=0"""
        # Submit print without waiting for output
        self.mpy.comm.exec("print('mount_read_test')", timeout=0)
        import time
        time.sleep(0.1)
        # Read output via conn.read()
        data = self.mpy.conn.read(timeout=0.5)
        self.assertIsNotNone(data)
        self.assertIn(b'mount_read_test', data)
        # Drain remaining output and restore REPL state
        while self.mpy.conn.read(timeout=0.2):
            pass
        self.mpy.stop()

    def test_22_stop(self):
        """Test mpy.stop() interrupts running program"""
        import time
        # Start a long-running program (timeout=0 = fire-and-forget)
        self.mpy.comm.exec(
            "import time\nwhile True:\n time.sleep(0.1)\n", timeout=0)
        time.sleep(0.3)
        # Stop it
        self.mpy.stop()
        # Now exec should work
        result = self.mpy.comm.exec_eval("1 + 1")
        self.assertEqual(result, 2)

    def test_23_stop_then_vfs(self):
        """Test VFS still works after stop()"""
        self.mpy.stop()
        result = self.mpy.comm.exec_eval(
            "repr(open('/remote/hello.txt').read())")
        self.assertEqual(result, 'Hello from mount test!')

    def test_24_read_vfs_transparent(self):
        """Test conn.read() services VFS requests transparently"""
        # Submit code that reads VFS file and prints result
        self.mpy.comm.exec(
            "print(open('/remote/hello.txt').read())", timeout=0)
        import time
        time.sleep(0.1)
        # read() should handle VFS + return print output
        output = b''
        for _ in range(50):
            data = self.mpy.conn.read(timeout=0.1)
            if data:
                output += data
            if b'Hello from mount test!' in output:
                break
        self.assertIn(b'Hello from mount test!', output)
        # Drain remaining and restore REPL state
        while self.mpy.conn.read(timeout=0.2):
            pass
        self.mpy.stop()


@requires_device
class TestMountLn(unittest.TestCase):
    """Test ln command: virtual submounts into mounted VFS"""

    LOCAL_DIR = None
    LN_DIR = None
    LN_FILE_DIR = None

    @classmethod
    def setUpClass(cls):
        import tempfile
        from mpytool import ConnSerial, Mpy

        # Main mount directory with a root file
        cls.LOCAL_DIR = tempfile.mkdtemp(prefix='mpytool_ln_root_')
        with open(os.path.join(cls.LOCAL_DIR, 'root.txt'), 'w') as f:
            f.write('root file')

        # Separate directory to link as submount
        cls.LN_DIR = tempfile.mkdtemp(prefix='mpytool_ln_pkg_')
        with open(os.path.join(cls.LN_DIR, 'mod.py'), 'w') as f:
            f.write('LN_VALUE = 77\n')
        subpkg = os.path.join(cls.LN_DIR, 'inner')
        os.makedirs(subpkg)
        with open(os.path.join(subpkg, 'deep.txt'), 'w') as f:
            f.write('deep content')

        # Single file to link
        cls.LN_FILE_DIR = tempfile.mkdtemp(prefix='mpytool_ln_file_')
        with open(os.path.join(cls.LN_FILE_DIR, 'single.py'), 'w') as f:
            f.write('SINGLE = 123\n')

        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        cls.handler = cls.mpy.mount(cls.LOCAL_DIR, '/lntest')

        # Add submounts via API (what ln command does internally)
        cls.mpy.add_submount('/lntest', 'lib/pkg', cls.LN_DIR)
        cls.mpy.add_submount(
            '/lntest', 'lib/single.py',
            os.path.join(cls.LN_FILE_DIR, 'single.py'))

    @classmethod
    def tearDownClass(cls):
        import shutil
        try:
            cls.mpy.comm.enter_raw_repl()
            cls.mpy.comm.exec(
                "import uos\ntry:uos.umount('/lntest')\nexcept:pass\n"
                "uos.chdir('/')", timeout=3)
        except Exception:
            pass
        try:
            cls.mpy.comm.exit_raw_repl()
        except Exception:
            pass
        cls.conn.close()
        for d in (cls.LOCAL_DIR, cls.LN_DIR, cls.LN_FILE_DIR):
            if d:
                shutil.rmtree(d, ignore_errors=True)

    def test_01_root_still_works(self):
        """Root mount files still accessible after adding submounts"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/lntest/root.txt').read())")
        self.assertEqual(result, 'root file')

    def test_02_listdir_root_shows_virtual(self):
        """listdir root includes virtual 'lib' directory from submount"""
        result = self.mpy.comm.exec_eval(
            "repr(__import__('os').listdir('/lntest'))")
        self.assertIn('root.txt', result)
        self.assertIn('lib', result)

    def test_03_listdir_submount_dir(self):
        """listdir on submount shows linked directory contents"""
        result = self.mpy.comm.exec_eval(
            "repr(__import__('os').listdir('/lntest/lib/pkg'))")
        self.assertIn('mod.py', result)
        self.assertIn('inner', result)

    def test_04_read_submount_file(self):
        """Read file from linked directory"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/lntest/lib/pkg/mod.py').read())")
        self.assertEqual(result, 'LN_VALUE = 77\n')

    def test_05_read_deep_nested(self):
        """Read file in subdirectory of linked directory"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/lntest/lib/pkg/inner/deep.txt').read())")
        self.assertEqual(result, 'deep content')

    def test_06_stat_submount_dir(self):
        """stat on submount directory returns S_IFDIR"""
        result = self.mpy.comm.exec_eval(
            "__import__('os').stat('/lntest/lib/pkg')")
        self.assertTrue(result[0] & 0x4000)

    def test_07_stat_submount_file(self):
        """stat on submount file returns S_IFREG"""
        result = self.mpy.comm.exec_eval(
            "__import__('os').stat('/lntest/lib/pkg/mod.py')")
        self.assertTrue(result[0] & 0x8000)

    def test_08_single_file_link(self):
        """Read single file linked as submount"""
        result = self.mpy.comm.exec_eval(
            "repr(open('/lntest/lib/single.py').read())")
        self.assertEqual(result, 'SINGLE = 123\n')

    def test_09_stat_single_file(self):
        """stat on single file submount returns S_IFREG"""
        result = self.mpy.comm.exec_eval(
            "__import__('os').stat('/lntest/lib/single.py')")
        self.assertTrue(result[0] & 0x8000)

    def test_10_import_from_submount(self):
        """Import module from linked directory"""
        self.mpy.comm.exec(
            "import sys\n"
            "sys.path.append('/lntest/lib/pkg')\n"
            "for k in list(sys.modules):\n"
            " if k=='mod':del sys.modules[k]\n")
        result = self.mpy.comm.exec_eval("__import__('mod').LN_VALUE")
        self.assertEqual(result, 77)
        # Cleanup sys.path
        self.mpy.comm.exec(
            "import sys\n"
            "if '/lntest/lib/pkg' in sys.path:"
            "sys.path.remove('/lntest/lib/pkg')\n")

    def test_11_nonexistent_in_submount(self):
        """stat on nonexistent file in submount raises OSError"""
        from mpytool.mpy_comm import CmdError
        with self.assertRaises(CmdError) as ctx:
            self.mpy.comm.exec(
                "__import__('os').stat('/lntest/lib/pkg/nope.xyz')")
        self.assertIn('OSError', ctx.exception.error)

    def test_12_listdir_virtual_intermediate(self):
        """listdir on 'lib' (virtual intermediate dir) shows submount entries"""
        result = self.mpy.comm.exec_eval(
            "repr(__import__('os').listdir('/lntest/lib'))")
        self.assertIn('pkg', result)
        self.assertIn('single.py', result)


@requires_device
class TestPathOperations(unittest.TestCase):
    """Test sys.path operations"""

    @classmethod
    def setUpClass(cls):
        from mpytool import ConnSerial, Mpy
        cls.conn = ConnSerial(port=DEVICE_PORT, baudrate=115200)
        cls.mpy = Mpy(cls.conn)
        # Save original sys.path
        cls.original_path = cls.mpy.get_sys_path()

    @classmethod
    def tearDownClass(cls):
        # Restore original sys.path
        try:
            cls.mpy.set_sys_path(*cls.original_path)
        except Exception:
            pass
        cls.mpy.comm.exit_raw_repl()
        cls.conn.close()

    def setUp(self):
        # Reset to original path before each test
        self.mpy.set_sys_path(*self.original_path)

    def test_01_get_sys_path(self):
        """Test getting sys.path from device"""
        path = self.mpy.get_sys_path()
        self.assertIsInstance(path, list)
        self.assertGreater(len(path), 0)

    def test_02_set_sys_path(self):
        """Test setting entire sys.path"""
        self.mpy.set_sys_path('', '/lib')
        path = self.mpy.get_sys_path()
        self.assertEqual(path, ['', '/lib'])

    def test_03_prepend_sys_path(self):
        """Test prepending to sys.path"""
        self.mpy.set_sys_path('', '/lib')
        self.mpy.prepend_sys_path('/custom')
        path = self.mpy.get_sys_path()
        self.assertEqual(path, ['/custom', '', '/lib'])

    def test_04_append_sys_path(self):
        """Test appending to sys.path"""
        self.mpy.set_sys_path('', '/lib')
        self.mpy.append_sys_path('/extra')
        path = self.mpy.get_sys_path()
        self.assertEqual(path, ['', '/lib', '/extra'])

    def test_05_prepend_removes_duplicates(self):
        """Test that prepend removes existing occurrences"""
        self.mpy.set_sys_path('', '/lib', '/custom')
        self.mpy.prepend_sys_path('')  # Move '' to front
        path = self.mpy.get_sys_path()
        self.assertEqual(path[0], '')
        self.assertEqual(path.count(''), 1)

    def test_06_append_removes_duplicates(self):
        """Test that append removes existing occurrences"""
        self.mpy.set_sys_path('/custom', '', '/lib')
        self.mpy.append_sys_path('/custom')  # Move /custom to end
        path = self.mpy.get_sys_path()
        self.assertEqual(path[-1], '/custom')
        self.assertEqual(path.count('/custom'), 1)

    def test_07_remove_from_sys_path(self):
        """Test removing paths from sys.path"""
        self.mpy.set_sys_path('', '/lib', '/custom', '/extra')
        self.mpy.remove_from_sys_path('/custom', '/extra')
        path = self.mpy.get_sys_path()
        self.assertNotIn('/custom', path)
        self.assertNotIn('/extra', path)
        self.assertIn('', path)
        self.assertIn('/lib', path)

    def test_08_remove_nonexistent_path(self):
        """Test removing nonexistent path doesn't raise error"""
        self.mpy.set_sys_path('', '/lib')
        self.mpy.remove_from_sys_path('/nonexistent')
        path = self.mpy.get_sys_path()
        self.assertEqual(path, ['', '/lib'])

    def test_09_prepend_multiple_paths(self):
        """Test prepending multiple paths at once"""
        self.mpy.set_sys_path('', '/lib')
        self.mpy.prepend_sys_path('/a', '/b')
        path = self.mpy.get_sys_path()
        self.assertEqual(path[:2], ['/a', '/b'])

    def test_10_append_multiple_paths(self):
        """Test appending multiple paths at once"""
        self.mpy.set_sys_path('', '/lib')
        self.mpy.append_sys_path('/a', '/b')
        path = self.mpy.get_sys_path()
        self.assertEqual(path[-2:], ['/a', '/b'])


if __name__ == "__main__":
    unittest.main()
