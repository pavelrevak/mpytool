"""CLI end-to-end tests for mpytool

These tests run mpytool as subprocess and verify stdout/stderr/exit code.

Run all tests (no device needed for basic tests):
    python -m unittest tests.test_cli -v

Run with device (read-only tests):
    MPYTOOL_TEST_PORT=/dev/cu.usbmodem1101 python -m unittest tests.test_cli -v

Run all device tests (dedicated test device):
    MPYTOOL_TEST_PORT_RW=/dev/cu.usbmodem1101 python -m unittest tests.test_cli -v
"""

import os
import subprocess
import tempfile
import shutil
import unittest


# Test ports for different test levels
PORT_RO = os.environ.get("MPYTOOL_TEST_PORT")
PORT_RW = os.environ.get("MPYTOOL_TEST_PORT_RW")

# RW implies RO
if PORT_RW:
    PORT_RO = PORT_RO or PORT_RW


def run_mpytool(*args, timeout=10):
    """Run mpytool CLI and return CompletedProcess"""
    cmd = ['mpytool'] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout)


def requires_device(cls):
    """Read-only tests - safe on any device"""
    if not PORT_RO:
        return unittest.skip("MPYTOOL_TEST_PORT not set")(cls)
    cls.DEVICE_PORT = PORT_RO
    return cls


def requires_device_rw(cls):
    """Tests that write to /_mpytool_cli_test/ directory"""
    if not PORT_RW:
        return unittest.skip("MPYTOOL_TEST_PORT_RW not set")(cls)
    cls.DEVICE_PORT = PORT_RW
    return cls


# =============================================================================
# Tests without device (always run)
# =============================================================================

class TestCliHelp(unittest.TestCase):
    """Test --help output (no device needed)"""

    def test_main_help(self):
        """mpytool --help shows usage"""
        result = run_mpytool('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('usage:', result.stdout.lower())
        self.assertIn('-p', result.stdout)
        self.assertIn('--port', result.stdout)

    def test_main_help_short(self):
        """mpytool -h shows usage"""
        result = run_mpytool('-h')
        self.assertEqual(result.returncode, 0)
        self.assertIn('usage:', result.stdout.lower())

    def test_version(self):
        """mpytool --version shows version"""
        result = run_mpytool('--version')
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout.strip(), r'\d+\.\d+')

    def test_ls_help(self):
        """mpytool ls --help shows ls usage"""
        result = run_mpytool('ls', '--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('list', result.stdout.lower())

    def test_cp_help(self):
        """mpytool cp --help shows cp usage and remote path syntax"""
        result = run_mpytool('cp', '--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn(':', result.stdout)

    def test_mv_help(self):
        """mpytool mv --help shows mv usage"""
        result = run_mpytool('mv', '--help')
        self.assertEqual(result.returncode, 0)

    def test_rm_help(self):
        """mpytool rm --help shows rm usage"""
        result = run_mpytool('rm', '--help')
        self.assertEqual(result.returncode, 0)

    def test_mkdir_help(self):
        """mpytool mkdir --help shows mkdir usage"""
        result = run_mpytool('mkdir', '--help')
        self.assertEqual(result.returncode, 0)

    def test_cat_help(self):
        """mpytool cat --help shows cat usage"""
        result = run_mpytool('cat', '--help')
        self.assertEqual(result.returncode, 0)

    def test_tree_help(self):
        """mpytool tree --help shows tree usage"""
        result = run_mpytool('tree', '--help')
        self.assertEqual(result.returncode, 0)

    def test_info_help(self):
        """mpytool info --help shows info usage"""
        result = run_mpytool('info', '--help')
        self.assertEqual(result.returncode, 0)

    def test_reset_help(self):
        """mpytool reset --help shows reset modes"""
        result = run_mpytool('reset', '--help')
        self.assertEqual(result.returncode, 0)
        # Should mention reset modes
        self.assertTrue(
            'soft' in result.stdout.lower() or
            'hard' in result.stdout.lower())

    def test_mount_help(self):
        """mpytool mount --help shows mount usage"""
        result = run_mpytool('mount', '--help')
        self.assertEqual(result.returncode, 0)

    def test_exec_help(self):
        """mpytool exec --help shows exec usage"""
        result = run_mpytool('exec', '--help')
        self.assertEqual(result.returncode, 0)

    def test_run_help(self):
        """mpytool run --help shows run usage"""
        result = run_mpytool('run', '--help')
        self.assertEqual(result.returncode, 0)

    def test_flash_help(self):
        """mpytool flash --help shows flash usage"""
        result = run_mpytool('flash', '--help')
        self.assertEqual(result.returncode, 0)

    def test_repl_help(self):
        """mpytool repl --help shows repl usage"""
        result = run_mpytool('repl', '--help')
        self.assertEqual(result.returncode, 0)

    def test_monitor_help(self):
        """mpytool monitor --help shows monitor usage"""
        result = run_mpytool('monitor', '--help')
        self.assertEqual(result.returncode, 0)


class TestCliErrors(unittest.TestCase):
    """Test error handling (no device needed)"""

    def test_unknown_command(self):
        """Unknown command shows error"""
        result = run_mpytool('-p', '/dev/null', 'neexistuje')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unknown', result.stderr.lower())

    def test_invalid_port(self):
        """Invalid port shows error"""
        result = run_mpytool('-p', '/dev/neexistujuci_port_xyz', 'ls')
        self.assertNotEqual(result.returncode, 0)

    def test_port_and_address_conflict(self):
        """Cannot use both -p and -a"""
        result = run_mpytool('-p', '/dev/tty0', '-a', '192.168.1.1', 'ls')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('only', result.stderr.lower())

    def test_no_command(self):
        """No command shows help or error"""
        result = run_mpytool()
        # Should show help (exit 0) or require command (exit != 0)
        # Both are acceptable behaviors
        self.assertTrue(
            result.returncode == 0 or
            'usage' in result.stdout.lower() or
            'usage' in result.stderr.lower() or
            len(result.stderr) > 0)


class TestCliCompletion(unittest.TestCase):
    """Test hidden completion commands (no device needed)"""

    def test_commands_list(self):
        """_commands returns command list"""
        result = run_mpytool('_commands')
        self.assertEqual(result.returncode, 0)
        output = result.stdout
        self.assertIn('ls', output)
        self.assertIn('cp', output)
        self.assertIn('repl', output)
        self.assertIn('mount', output)

    def test_options_list(self):
        """_options returns global options"""
        result = run_mpytool('_options')
        self.assertEqual(result.returncode, 0)
        output = result.stdout
        self.assertIn('-p', output)
        self.assertIn('--port', output)
        self.assertIn('-v', output)
        self.assertIn('--verbose', output)


# =============================================================================
# Read-only tests with device
# =============================================================================

@requires_device
class TestCliReadOnly(unittest.TestCase):
    """Read-only CLI tests with device"""

    def test_ls_root(self):
        """ls :/ lists root directory"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'ls', ':/')
        self.assertEqual(result.returncode, 0)

    def test_ls_cwd(self):
        """ls : lists current directory"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'ls', ':')
        self.assertEqual(result.returncode, 0)

    def test_ls_default(self):
        """ls without path lists current directory"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'ls')
        self.assertEqual(result.returncode, 0)

    def test_tree_root(self):
        """tree :/ shows tree structure"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'tree', ':/')
        self.assertEqual(result.returncode, 0)

    def test_pwd(self):
        """pwd shows current directory"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'pwd')
        self.assertEqual(result.returncode, 0)
        self.assertIn('/', result.stdout)

    def test_info(self):
        """info shows device information"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'info')
        self.assertEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        # Should contain some device info
        self.assertTrue(len(output) > 10)

    def test_info_verbose(self):
        """info -v shows more details"""
        result = run_mpytool('-p', self.DEVICE_PORT, '-v', 'info')
        self.assertEqual(result.returncode, 0)

    def test_exec_simple(self):
        """exec runs Python code"""
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'exec', 'print(1+1)')
        self.assertEqual(result.returncode, 0)
        self.assertIn('2', result.stdout)

    def test_exec_import(self):
        """exec can import modules"""
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'exec', 'import sys; print(sys.platform)')
        self.assertEqual(result.returncode, 0)
        self.assertTrue(len(result.stdout.strip()) > 0)

    def test_cat_nonexistent(self):
        """cat nonexistent file shows error"""
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cat', ':/_nonexistent_file_xyz123.txt')
        self.assertNotEqual(result.returncode, 0)

    def test_quiet_mode(self):
        """Quiet mode reduces output"""
        result = run_mpytool('-p', self.DEVICE_PORT, '-q', 'ls', ':/')
        self.assertEqual(result.returncode, 0)

    def test_verbose_mode(self):
        """Verbose mode shows more output"""
        result = run_mpytool('-p', self.DEVICE_PORT, '-v', 'ls', ':/')
        self.assertEqual(result.returncode, 0)

    def test_debug_mode(self):
        """Debug mode shows debug info"""
        result = run_mpytool('-p', self.DEVICE_PORT, '-d', 'pwd')
        self.assertEqual(result.returncode, 0)
        # Debug output typically goes to stderr
        combined = result.stdout + result.stderr
        self.assertTrue(len(combined) > 0)

    def test_command_chaining(self):
        """Multiple commands can be chained with --"""
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'pwd', '--', 'ls', ':/')
        self.assertEqual(result.returncode, 0)

    def test_sleep(self):
        """sleep pauses execution"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'sleep', '0.1')
        self.assertEqual(result.returncode, 0)

    def test_stop(self):
        """stop sends Ctrl-C to device"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'stop')
        self.assertEqual(result.returncode, 0)

    def test_flash_info(self):
        """flash shows flash/partition info"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'flash')
        self.assertEqual(result.returncode, 0)
        # Should show some flash info
        output = result.stdout + result.stderr
        self.assertTrue(len(output) > 0)

    def test_rtc_read(self):
        """rtc shows current RTC time"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'rtc')
        self.assertEqual(result.returncode, 0)
        # Should show date/time or "RTC not available"
        output = result.stdout + result.stderr
        self.assertTrue(len(output) > 0)

    def test_path_show(self):
        """path shows sys.path"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'path')
        self.assertEqual(result.returncode, 0)


@requires_device
class TestCliCatBoot(unittest.TestCase):
    """Test cat command on common files (read-only)"""

    def test_cat_boot_py(self):
        """cat :boot.py or :/boot.py if exists"""
        # Try both locations
        result = run_mpytool('-p', self.DEVICE_PORT, 'cat', ':/boot.py')
        if result.returncode != 0:
            result = run_mpytool('-p', self.DEVICE_PORT, 'cat', ':boot.py')
        # May or may not exist, but command should work
        # (either success or "not found" error)
        self.assertTrue(
            result.returncode == 0 or
            'not found' in result.stderr.lower())


# =============================================================================
# Write tests with device (isolated to test directory)
# =============================================================================

@requires_device_rw
class TestCliWrite(unittest.TestCase):
    """CLI tests that write to device (uses /_mpytool_cli_test/)"""

    TEST_DIR = '/_mpytool_cli_test'

    @classmethod
    def setUpClass(cls):
        """Create test directory on device"""
        run_mpytool('-p', cls.DEVICE_PORT, 'mkdir', f':{cls.TEST_DIR}')

    @classmethod
    def tearDownClass(cls):
        """Remove test directory from device"""
        run_mpytool('-p', cls.DEVICE_PORT, 'rm', f':{cls.TEST_DIR}')

    def setUp(self):
        """Create temp directory for local files"""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Remove local temp directory"""
        shutil.rmtree(self.temp_dir)

    def test_cp_upload_single_file(self):
        """cp uploads single file"""
        # Create local file
        local_file = os.path.join(self.temp_dir, 'test.txt')
        with open(local_file, 'w') as f:
            f.write('hello from cli test')
        # Upload
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cp', local_file,
            f':{self.TEST_DIR}/')
        self.assertEqual(result.returncode, 0)
        # Verify
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cat',
            f':{self.TEST_DIR}/test.txt')
        self.assertEqual(result.returncode, 0)
        self.assertIn('hello from cli test', result.stdout)
        # Cleanup
        run_mpytool(
            '-p', self.DEVICE_PORT, 'rm',
            f':{self.TEST_DIR}/test.txt')

    def test_cp_upload_with_verbose(self):
        """cp -v shows progress"""
        local_file = os.path.join(self.temp_dir, 'verbose.txt')
        with open(local_file, 'w') as f:
            f.write('test content')
        result = run_mpytool(
            '-p', self.DEVICE_PORT, '-v', 'cp', local_file,
            f':{self.TEST_DIR}/')
        self.assertEqual(result.returncode, 0)
        # Verbose should show something about the file
        combined = result.stdout + result.stderr
        self.assertTrue(
            'verbose.txt' in combined or
            '[1/1]' in combined)
        # Cleanup
        run_mpytool(
            '-p', self.DEVICE_PORT, 'rm',
            f':{self.TEST_DIR}/verbose.txt')

    def test_cp_download_single_file(self):
        """cp downloads single file"""
        # First upload a file
        local_file = os.path.join(self.temp_dir, 'upload.txt')
        with open(local_file, 'w') as f:
            f.write('download test')
        run_mpytool(
            '-p', self.DEVICE_PORT, 'cp', local_file,
            f':{self.TEST_DIR}/')
        # Download to different name
        download_path = os.path.join(self.temp_dir, 'downloaded.txt')
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cp',
            f':{self.TEST_DIR}/upload.txt', download_path)
        self.assertEqual(result.returncode, 0)
        # Verify content
        with open(download_path, 'r') as f:
            content = f.read()
        self.assertEqual(content, 'download test')
        # Cleanup
        run_mpytool(
            '-p', self.DEVICE_PORT, 'rm',
            f':{self.TEST_DIR}/upload.txt')

    def test_mkdir_and_rm(self):
        """mkdir creates directory, rm removes it"""
        subdir = f'{self.TEST_DIR}/subdir'
        # Create
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'mkdir', f':{subdir}')
        self.assertEqual(result.returncode, 0)
        # Verify exists
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'ls', f':{self.TEST_DIR}')
        self.assertEqual(result.returncode, 0)
        self.assertIn('subdir', result.stdout)
        # Remove
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'rm', f':{subdir}')
        self.assertEqual(result.returncode, 0)

    def test_mv_rename(self):
        """mv renames file"""
        # Upload file
        local_file = os.path.join(self.temp_dir, 'original.txt')
        with open(local_file, 'w') as f:
            f.write('rename test')
        run_mpytool(
            '-p', self.DEVICE_PORT, 'cp', local_file,
            f':{self.TEST_DIR}/')
        # Rename
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'mv',
            f':{self.TEST_DIR}/original.txt',
            f':{self.TEST_DIR}/renamed.txt')
        self.assertEqual(result.returncode, 0)
        # Verify old name gone
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cat',
            f':{self.TEST_DIR}/original.txt')
        self.assertNotEqual(result.returncode, 0)
        # Verify new name exists
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cat',
            f':{self.TEST_DIR}/renamed.txt')
        self.assertEqual(result.returncode, 0)
        self.assertIn('rename test', result.stdout)
        # Cleanup
        run_mpytool(
            '-p', self.DEVICE_PORT, 'rm',
            f':{self.TEST_DIR}/renamed.txt')

    def test_cp_directory(self):
        """cp uploads directory"""
        # Create local directory with files
        local_subdir = os.path.join(self.temp_dir, 'mylib')
        os.makedirs(local_subdir)
        with open(os.path.join(local_subdir, 'a.py'), 'w') as f:
            f.write('# a')
        with open(os.path.join(local_subdir, 'b.py'), 'w') as f:
            f.write('# b')
        # Upload directory
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cp', local_subdir,
            f':{self.TEST_DIR}/')
        self.assertEqual(result.returncode, 0)
        # Verify
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'ls',
            f':{self.TEST_DIR}/mylib')
        self.assertEqual(result.returncode, 0)
        self.assertIn('a.py', result.stdout)
        self.assertIn('b.py', result.stdout)
        # Cleanup
        run_mpytool(
            '-p', self.DEVICE_PORT, 'rm',
            f':{self.TEST_DIR}/mylib')

    def test_cp_force_overwrites(self):
        """cp -f overwrites existing file"""
        local_file = os.path.join(self.temp_dir, 'force.txt')
        remote_path = f':{self.TEST_DIR}/force.txt'
        # Upload v1
        with open(local_file, 'w') as f:
            f.write('version 1')
        run_mpytool('-p', self.DEVICE_PORT, 'cp', local_file, remote_path)
        # Upload v2 without -f (should skip)
        with open(local_file, 'w') as f:
            f.write('version 2')
        run_mpytool('-p', self.DEVICE_PORT, 'cp', local_file, remote_path)
        result = run_mpytool('-p', self.DEVICE_PORT, 'cat', remote_path)
        # May or may not be updated (depends on hash)
        # Upload v3 with -f (should overwrite)
        with open(local_file, 'w') as f:
            f.write('version 3')
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'cp', '-f', local_file, remote_path)
        self.assertEqual(result.returncode, 0)
        result = run_mpytool('-p', self.DEVICE_PORT, 'cat', remote_path)
        self.assertIn('version 3', result.stdout)
        # Cleanup
        run_mpytool('-p', self.DEVICE_PORT, 'rm', remote_path)

    def test_cd_and_pwd(self):
        """cd changes directory, pwd shows it"""
        # Create test subdir
        subdir = f'{self.TEST_DIR}/cdtest'
        run_mpytool('-p', self.DEVICE_PORT, 'mkdir', f':{subdir}')
        # Change to it
        result = run_mpytool('-p', self.DEVICE_PORT, 'cd', f':{subdir}')
        self.assertEqual(result.returncode, 0)
        # Verify with pwd
        result = run_mpytool('-p', self.DEVICE_PORT, 'pwd')
        self.assertEqual(result.returncode, 0)
        self.assertIn('cdtest', result.stdout)
        # Change back to root
        run_mpytool('-p', self.DEVICE_PORT, 'cd', ':/')
        # Cleanup
        run_mpytool('-p', self.DEVICE_PORT, 'rm', f':{subdir}')

    def test_path_modify(self):
        """path can modify sys.path"""
        # Get original path
        result = run_mpytool('-p', self.DEVICE_PORT, 'path')
        self.assertEqual(result.returncode, 0)
        # Append test path
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'path', '-a', ':/testpath')
        self.assertEqual(result.returncode, 0)
        # Verify it's added
        result = run_mpytool('-p', self.DEVICE_PORT, 'path')
        self.assertIn('testpath', result.stdout)
        # Remove it
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'path', '-d', ':/testpath')
        self.assertEqual(result.returncode, 0)

    def test_rtc_set(self):
        """rtc --set sets RTC to local time"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'rtc', '--set')
        # May fail on devices without RTC
        if result.returncode == 0:
            self.assertIn('set', result.stderr.lower())

    def test_reset_soft(self):
        """reset performs soft reset"""
        result = run_mpytool('-p', self.DEVICE_PORT, 'reset', timeout=15)
        self.assertEqual(result.returncode, 0)


@requires_device_rw
class TestCliRun(unittest.TestCase):
    """Test run command (sends script to device, no output capture)"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_run_script(self):
        """run sends script to device (timeout=0, no output)"""
        # run command just sends code, doesn't wait for output
        # Use a script that sets a variable we can check via exec
        script = os.path.join(self.temp_dir, 'test_script.py')
        with open(script, 'w') as f:
            f.write('_cli_test_var = 12345\n')
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'run', script, timeout=15)
        self.assertEqual(result.returncode, 0)
        # Verify script ran by checking variable
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'exec', 'print(_cli_test_var)',
            timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('12345', result.stdout)

    def test_run_nonexistent_file(self):
        """run with nonexistent file shows error"""
        result = run_mpytool(
            '-p', self.DEVICE_PORT, 'run', '/nonexistent/script.py',
            timeout=10)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
