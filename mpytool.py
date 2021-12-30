"""MicroPython tool"""

import os as _os
import sys as _sys
import time as _time
import argparse as _argparse
import serial as _serial


class MpyError(Exception):
    """General MPY error"""


class Timeout(MpyError):
    """Timeout"""


class ParamsError(MpyError):
    """Timeout"""


class CmdError(MpyError):
    """Timeout"""
    def __init__(self, cmd, result, error):
        self._cmd = cmd
        self._result = result
        self._error = error.decode('utf-8')
        super().__init__(self.__str__())

    def __str__(self):
        res = f'Command:\n  {self._cmd}\n'
        if self._result:
            res += f'Result:\n  {self._result}\n'
        if self._error:
            res += f'Error:\n  {self._error}'
        return res

    @property
    def cmd(self):
        return self._cmd

    @property
    def result(self):
        return self._result

    @property
    def error(self):
        return self._error


class PathNotFound(MpyError):
    """File not found"""
    def __init__(self, file_name):
        self._file_name = file_name
        super().__init__(self.__str__())

    def __str__(self):
        return f"Path '{self._file_name}' was not found"


class FileNotFound(PathNotFound):
    """Folder not found"""
    def __str__(self):
        return f"File '{self._file_name}' was not found"


class DirNotFound(PathNotFound):
    """Folder not found"""
    def __str__(self):
        return f"Dir '{self._file_name}' was not found"


class SerialConn():
    def __init__(self, log=None, **serial_config):
        self._log = log
        self._serial = _serial.Serial(**serial_config)
        self._buffer = b''

    def _read_to_buffer(self):
        in_waiting = self._serial.in_waiting
        if in_waiting > 0:
            self._buffer += self._serial.read(in_waiting)
            return True
        return False

    def write(self, data, chunk_size=128, delay=0.01):
        if self._log:
            self._log.debug(f"wr: {data}")
        while data:
            chunk = data[:chunk_size]
            count = self._serial.write(chunk)
            data = data[count:]
            _time.sleep(delay)

    def read_until(self, end, timeout=1):
        self._log.debug(f'wait for {end}')
        start_time = _time.time()
        while True:
            if self._read_to_buffer():
                start_time = _time.time()
            if end in self._buffer:
                break
            if timeout is not None and start_time + timeout < _time.time():
                if self._buffer:
                    raise Timeout(
                        f"During timeout received: {bytes(self._buffer)}")
                raise Timeout("No data received")
            _time.sleep(.01)
        data, self._buffer = self._buffer.split(end, 1)
        if self._log:
            self._log.debug(f"rd: {data + end}")
        data = data.rstrip(end)
        return data

    def read_line(self, timeout=None):
        line = self.read_until(b'\n', timeout)
        return line.strip(b'\r')


class MpyComm():
    def __init__(self, conn, log=None):
        self._conn = conn
        self._log = log
        self._repl_mode = None

    @property
    def conn(self):
        return self._conn

    def stop_current_operation(self):
        if self._repl_mode is not None:
            return
        if self._log:
            self._log.info('STOP CURRENT OPERATION')
        self._conn.write(b'\x03')
        try:
            # wait for prompt
            self._conn.read_until(b'\r\n>>> ', timeout=1)
        except Timeout:
            # probably is in RAW repl
            self._log.warning("Timeout while stopping program")
            self.exit_raw_repl()

    def enter_raw_repl(self):
        if self._repl_mode is True:
            return
        self.stop_current_operation()
        if self._log:
            self._log.info('ENTER RAW REPL')
        self._conn.write(b'\x01')
        self._conn.read_until(b'\r\n>')
        self._repl_mode = True

    def exit_raw_repl(self):
        if not self._repl_mode:
            return
        if self._log:
            self._log.info('EXIT RAW REPL')
        self._conn.write(b'\x02')
        self._conn.read_until(b'\r\n>>> ')
        self._repl_mode = False

    def soft_reset(self):
        self.stop_current_operation()
        self.exit_raw_repl()
        if self._log:
            self._log.info('SOFT RESET')
        self._conn.write(b'\x04')
        self._conn.read_until(b'soft reboot', timeout=1)
        self._repl_mode = None

    def exec(self, command, timeout=5):
        """Execute command

        Arguments:
            command: command to execute
            timeout: maximum waiting time for result

        Returns:
            command STDOUT result

        Raises:
            CmdError when command return error
        """
        # wait for prompt
        self.enter_raw_repl()
        if self._log:
            self._log.info(f"CMD: {command}")
        self._conn.write(bytes(command, 'utf-8'))
        self._conn.write(b'\x04')
        self._conn.read_until(b'OK', timeout)
        result = self._conn.read_until(b'\x04', timeout)
        if result:
            if self._log:
                self._log.info(f'RES: {result}')
        err = self._conn.read_until(b'\x04>', timeout)
        if err:
            raise CmdError(command, result, err)
        return result

    def exec_eval(self, command, timeout=5):
        result = self.exec(f'print({command})', timeout)
        return eval(result)


class Mpy():
    _CHUNK = 4096
    _ATTR_DIR = 0x4000
    _ATTR_FILE = 0x8000
    _HELPERS = {
        'stat': f"""
def _mpytool_stat(path):
    try:
        res = os.stat(path)
        if res[0] == {_ATTR_DIR}:
            return -1
        if res[0] == {_ATTR_FILE}:
            return res[6]
    except:
        return None
    return None
""",
        'tree': f"""
def _mpytool_tree(path):
    res_dir = []
    res_file = []
    dir_size = 0
    for entry in os.ilistdir(path):
        name, attr = entry[:2]
        if attr == {_ATTR_FILE}:
            size = entry[3]
            res_file.append((name, size, None))
            dir_size += size
        elif attr == {_ATTR_DIR}:
            if path in ('', '/'):
                sub_path = path + name
            else:
                sub_path = path + '/' + name
            _sub_path, sub_dir_size, sub_tree = tree(sub_path)
            res_dir.append((name, sub_dir_size, sub_tree))
            dir_size += sub_dir_size
    return path, dir_size, res_dir + res_file
""",
        'mkdir': f"""
def _mpytool_mkdir(path):
    check_path = ''
    found = True
    for dir_part in path.split('/'):
        if check_path:
            check_path += '/'
        check_path += dir_part
        if found:
            try:
                result = os.stat(check_path)
                if result[0] == {_ATTR_FILE}:
                    return True
                continue
            except:
                found = False
        os.mkdir(check_path)
""",
        'rmdir': f"""
def _mpytool_rmdir(path):
    for name, attr, _inode, _size in os.ilistdir(path):
        if attr == {_ATTR_FILE}:
            os.remove(path + '/' + name)
        elif attr == {_ATTR_DIR}:
            rmdir(path + '/' + name)
    os.rmdir(path)
""",
        'get': f"""
def _mpytool_get(path):
    f = open(path, 'rb')
    for name, attr, _inode, _size in os.ilistdir(path):
        if attr == {_ATTR_FILE}:
            os.remove(path + '/' + name)
        elif attr == {_ATTR_DIR}:
            rmdir(path + '/' + name)
    os.rmdir(path)
"""}

    def __init__(self, conn, log=None):
        self._conn = conn
        self._log = log
        self._mpy_comm = MpyComm(conn, log=log)
        self._imported = []
        self._load_helpers = []

    @property
    def conn(self):
        return self._conn

    @property
    def comm(self):
        return self._mpy_comm

    def load_helper(self, helper):
        if helper not in self._load_helpers:
            if helper not in self._HELPERS:
                raise MpyError(f'Helper {helper} not defined')
            self._mpy_comm.exec(self._HELPERS[helper])
            self._load_helpers.append(helper)

    def import_module(self, module):
        if module not in self._imported:
            self._mpy_comm.exec(f'import {module}')
            self._imported.append(module)

    def stat(self, path):
        self.import_module('os')
        self.load_helper('stat')
        return self._mpy_comm.exec_eval(f"_mpytool_stat('{path}')")

    def ls(self, dir_name):
        self.import_module('os')
        try:
            result = self._mpy_comm.exec_eval(
                f"tuple(os.ilistdir('{dir_name}'))")
            res_dir = []
            res_file = []
            for entry in result:
                name, attr = entry[:2]
                if attr == self._ATTR_DIR:
                    res_dir.append((name, None))
                elif attr == self._ATTR_FILE:
                    size = entry[3]
                    res_file.append((name, size))
        except CmdError as err:
            raise DirNotFound(dir_name) from err
        return res_dir + res_file

    def tree(self, path):
        """Tree of directory structure with sizes

        Returns: entry of directory or file
            for directory:
                (dir_name, size, [list of sub-entries])
            for empty directory:
                (dir_name, size, [])
            for file:
                (dir_name, size, None)
        """
        self.import_module('os')
        self.load_helper('tree')
        if path in ('', '.', '/'):
            return self._mpy_comm.exec_eval(f"_mpytool_tree('{path}')")
        # check if path exists
        result = self.stat(path)
        if result is None:
            raise DirNotFound(path)
        if result == -1:
            return self._mpy_comm.exec_eval(f"_mpytool_tree('{path}')")
        return((path, result[6], None))

    def mkdir(self, path):
        self.import_module('os')
        self.load_helper('mkdir')
        if self._mpy_comm.exec_eval(f"_mpytool_mkdir('{path}')"):
            raise MpyError(f'Error creating directory, this is file: {path}')

    def delete(self, path):
        result = self.stat(path)
        if result is None:
            raise PathNotFound(path)
        if result == -1:
            self.import_module('os')
            self.load_helper('rmdir')
            self._mpy_comm.exec(f"_mpytool_rmdir('{path}')")
        else:
            self._mpy_comm.exec(f"os.remove('{path}')")

    def get(self, file_name):
        try:
            self._mpy_comm.exec(f"f = open('{file_name}', 'rb')")
        except CmdError as err:
            raise FileNotFound(file_name) from err
        data = b''
        while True:
            result = self._mpy_comm.exec_eval(f"f.read({self._CHUNK})")
            if not result:
                break
            data += result
        self._mpy_comm.exec("f.close()")
        return data

    def put(self, data, file_name):
        path = file_name.rsplit('/', 1)[0]
        result = self.stat(path)
        if result is None:
            self.mkdir(path)
        elif result >= 0:
            raise MpyError(
                f'Error creating file under file: {path}')
        self._mpy_comm.exec(f"f = open('{file_name}', 'wb')")
        while data:
            chunk = data[:self._CHUNK]
            count = self._mpy_comm.exec_eval(f"f.write({chunk})", timeout=10)
            data = data[count:]
        self._mpy_comm.exec("f.close()")


class MpyTool():
    def __init__(self, conn, log=None, verbose=0):
        self._conn = conn
        self._log = log
        self._verbose = verbose
        self._mpy = Mpy(conn, log=log)

    @staticmethod
    def _print_ls(ls):
        for name, size in ls:
            if size is not None:
                print(f'{size:8d} {name}')
            else:
                print(f'{"":8} {name}/')

    def cmd_ls(self, dir_name):
        result = self._mpy.ls(dir_name)
        self._print_ls(result)

    SPACE = '   '
    BRANCH = '│  '
    TEE = '├─ '
    LAST = '└─ '

    @classmethod
    def _print_tree(cls, tree, prefix='', print_size=True, first=True, last=True):
        """Print tree of files
        """
        name, size, sub_tree = tree
        this_prefix = ''
        if not first:
            if last:
                this_prefix = cls.LAST
            else:
                this_prefix = cls.TEE
        sufix = ''
        if sub_tree is not None and name != ('/'):
            sufix = '/'
        line = ''
        if print_size:
            line += f'{size:8d} '
        line += prefix + this_prefix + name + sufix
        print(line)
        if not sub_tree:
            return
        sub_prefix = ''
        if not first:
            if last:
                sub_prefix = cls.SPACE
            else:
                sub_prefix = cls.BRANCH
        for entry in sub_tree[:-1]:
            cls._print_tree(
                entry,
                prefix=prefix + sub_prefix,
                print_size=print_size,
                first=False,
                last=False)
        cls._print_tree(
            sub_tree[-1],
            prefix=prefix + sub_prefix,
            print_size=print_size,
            first=False,
            last=True)

    def cmd_tree(self, dir_name):
        tree = self._mpy.tree(dir_name)
        self._print_tree(tree)

    def cmd_get(self, *file_names):
        for file_name in file_names:
            data = self._mpy.get(file_name)
            print(data.decode('utf-8'))

    def cmd_put(self, src_file_name, dst_file_name):
        src_file_name = _os.path.abspath(src_file_name)
        print(f"PUT: '{src_file_name}' to '{dst_file_name}'")
        with open(src_file_name, 'rb') as src_file:
            data = src_file.read()
            self._mpy.put(data, dst_file_name)

    def cmd_mkdir(self, *dir_names):
        for dir_name in dir_names:
            self._mpy.mkdir(dir_name)

    def cmd_delete(self, *file_names):
        for file_name in file_names:
            self._mpy.delete(file_name)

    def cmd_log(self):
        try:
            while True:
                line = self._conn.read_line()
                line = line.decode('utf-8')
                print(line)
        except KeyboardInterrupt:
            self._log.warning(' Exiting..')
            return

    def process_commands(self, commands):
        try:
            while commands:
                command = commands.pop(0)
                if command == 'ls':
                    if commands:
                        dir_name = commands.pop(0)
                        if dir_name != '/':
                            dir_name = dir_name.rstrip('/')
                        self.cmd_ls(dir_name)
                        continue
                    self.cmd_ls('.')
                elif command == 'tree':
                    if commands:
                        dir_name = commands.pop(0)
                        if dir_name != '/':
                            dir_name = dir_name.rstrip('/')
                        self.cmd_tree(dir_name)
                        continue
                    self.cmd_tree('.')
                elif command == 'get':
                    if commands:
                        self.cmd_get(*commands)
                        break
                    raise ParamsError('missing file name for get command')
                elif command == 'put':
                    if commands:
                        src_file_name = commands.pop(0)
                        dst_file_name = src_file_name
                        if commands:
                            dst_file_name = commands.pop(0)
                            if dst_file_name.endswith('/'):
                                # take file_name from source
                                file_name = src_file_name.rsplit('/')[-1]
                                dst_file_name += file_name
                        self.cmd_put(src_file_name, dst_file_name)
                    else:
                        raise ParamsError('missing file name for put command')
                elif command == 'mkdir':
                    self.cmd_mkdir(*commands)
                    break
                elif command == 'delete':
                    self.cmd_delete(*commands)
                    break
                elif command == 'reset':
                    self._mpy.comm.soft_reset()
                elif command == 'log':
                    self.cmd_log()
                    break
                else:
                    raise ParamsError(f"unknown command: '{command}'")
        except MpyError as err:
            self._log.error(err)
        self._mpy.comm.exit_raw_repl()


class SimpleColorLogger():
    def __init__(self, loglevel=0):
        self._loglevel = loglevel

    def log(self, msg):
        print(msg, file=_sys.stderr)

    def error(self, msg):
        if self._loglevel >= 1:
            self.log(f"\033[1;31m{msg}\033[0m")

    def warning(self, msg):
        if self._loglevel >= 2:
            self.log(f"\033[1;33m{msg}\033[0m")

    def info(self, msg):
        if self._loglevel >= 3:
            self.log(f"\033[1;35m{msg}\033[0m")

    def debug(self, msg):
        if self._loglevel >= 4:
            self.log(f"\033[1;34m{msg}\033[0m")


def main():
    parser = _argparse.ArgumentParser()
    parser.add_argument('-p', '--port', required=True, help="serial port")
    parser.add_argument(
        '-d', '--debug', default=0, action='count', help='set debug level')
    parser.add_argument(
        '-v', '--verbose', default=0, action='count', help='verbose output')
    parser.add_argument('commands', nargs='*', help='commands')
    args = parser.parse_args()

    log = SimpleColorLogger(args.debug + 1)
    serial_conn = SerialConn(port=args.port, baudrate=115200, log=log)
    mpy_tool = MpyTool(serial_conn, log=log, verbose=args.verbose)
    mpy_tool.process_commands(args.commands)


if __name__ == '__main__':
    main()
