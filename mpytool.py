"""MicroPython tool"""

import os
import sys
import time
import argparse as _argparse
import serial as _serial


class Timeout(Exception):
    """Timeout"""


class ParamsError(Exception):
    """Timeout"""


class CmdError(Exception):
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


class FileNotFound(Exception):
    """File not found"""
    def __init__(self, file_name):
        self._file_name = file_name
        super().__init__(self.__str__())

    def __str__(self):
        return f"File '{self._file_name}' was not found"


class DirNotFound(FileNotFound):
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

    def write(self, data):
        if self._log:
            self._log.debug(f"wr: {data}")
        self._serial.write(data)

    def read_until(self, end, timeout=1):
        self._log.debug(f'wait for {end}')
        start_time = time.time()
        while True:
            if self._read_to_buffer():
                start_time = time.time()
            if end in self._buffer:
                break
            if timeout is not None and start_time + timeout < time.time():
                if self._buffer:
                    raise Timeout(
                        f"During timeout received: {bytes(self._buffer)}")
                raise Timeout("No data received")
            time.sleep(.01)
        data, self._buffer = self._buffer.split(end, 1)
        if self._log:
            self._log.debug(f"rd: {data}")
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

    def enter_raw_repl(self):
        if self._repl_mode is True:
            return
        if self._log:
            self._log.info('ENTER RAW REPL')
        # stop current operations (in case if app running)
        self._conn.write(b'\x03')
        try:
            # wait for prompt
            self._conn.read_until(b'\r\n>>> ', timeout=.2)
        except Timeout:
            # probably is in RAW repl
            self._log.warning("Timeout while stopping program")
            self.exit_raw_repl()
        # enter raw repl
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
        if self._repl_mode is True:
            self.exit_raw_repl()
        if self._log:
            self._log.info('SOFT RESET')
        self._conn.write(b'\x04')
        self._repl_mode = None

    def exec(self, command, timeout=1):
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

    def exec_eval(self, command, timeout=1):
        result = self.exec(f'print({command})', timeout)
        return eval(result)


class Mpy():
    ATTR_DIR = 0x4000
    ATTR_FILE = 0x8000

    def __init__(self, conn, chunk_size=128, log=None):
        self._conn = conn
        self._chunk_size = chunk_size
        self._log = log
        self._mpy_comm = MpyComm(conn, log=log)
        self._imported = []

    @property
    def conn(self):
        return self._conn

    @property
    def comm(self):
        return self._mpy_comm

    def import_module(self, module):
        if module not in self._imported:
            self._mpy_comm.exec(f'import {module}')
            self._imported.append(module)

    def ls(self, dir_name, recursive=False):
        self.import_module('os')
        try:
            result = self._mpy_comm.exec_eval(
                f"tuple(os.ilistdir('{dir_name}'))")
            res_dir = []
            res_file = []
            for name, attr, _inode, size in result:
                if attr == self.ATTR_DIR:
                    res_dir.append(f'             {name}/')
                elif attr == self.ATTR_FILE:
                    res_file.append(f'{size:12} {name}')
        except CmdError as err:
            raise DirNotFound(dir_name) from err
        return res_dir + res_file

    def tree(self, dir_name, recursive=False):
        self.import_module('os')
        try:
            result = self._mpy_comm.exec_eval(
                f"tuple(os.ilistdir('{dir_name}'))")
            res_dir = []
            res_file = []
            dir_size = 0
            for name, attr, _inode, size in result:
                if attr == self.ATTR_DIR:
                    sub_dir_name = f'{dir_name}{name}/'
                    sub_dir_size, sub_dir = self.tree(
                        f'{sub_dir_name}', recursive)
                    dir_size += sub_dir_size
                    if recursive:
                        res_dir += sub_dir
                    else:
                        res_dir.append(f'{sub_dir_size:12} {sub_dir_name}')
                elif attr == self.ATTR_FILE:
                    file_name = f'{dir_name}{name}'
                    res_file.append(f'{size:12} {file_name}')
                    dir_size += size
            res_file.append(f'{dir_size:12} {dir_name}')
        except CmdError as err:
            raise DirNotFound(dir_name) from err
        return dir_size, res_dir + res_file

    def get(self, file_name):
        self._mpy_comm.exec(f"f = open('{file_name}', 'rb')")
        data = b''
        while True:
            result = self._mpy_comm.exec_eval(f"f.read({self._chunk_size})")
            if not result:
                break
            data += result
        self._mpy_comm.exec(f"f.close()")
        return data

    def put(self, data, file_name):
        self._mpy_comm.exec(f"f = open('{file_name}', 'wb')")
        while data:
            chunk = data[:self._chunk_size]
            count = self._mpy_comm.exec_eval(f"f.write({chunk})", timeout=10)
            data = data[count:]
        self._mpy_comm.exec("f.close()")


class MpyTool():
    def __init__(self, conn, log=None, verbose=0):
        self._conn = conn
        self._log = log
        self._mpy = Mpy(conn, chunk_size=128, log=log)

    def ls(self, dir_name):
        self._mpy.comm.enter_raw_repl()
        dir_name = dir_name.strip('/')
        if dir_name:
            dir_name = f'/{dir_name}/'
        else:
            dir_name = '/'
        try:
            result = self._mpy.ls(dir_name)
        except FileNotFound as err:
            self._log.error(err)
        else:
            for i in result:
                print(f"{i}")

    def tree(self, dir_name):
        self._mpy.comm.enter_raw_repl()
        dir_name = dir_name.strip('/')
        if dir_name:
            dir_name = f'/{dir_name}/'
        else:
            dir_name = '/'
        try:
            _size, result = self._mpy.tree(dir_name, recursive=True)
        except FileNotFound as err:
            self._log.error(err)
        else:
            for i in result:
                print(f"{i}")

    def get(self, *file_names):
        self._mpy.comm.enter_raw_repl()
        for file_name in file_names:
            if not file_name.startswith('/'):
                file_name = '/' + file_name
            data = self._mpy.get(file_name)
            print(data.decode('utf-8'))

    def put(self, *files):
        self._mpy.comm.enter_raw_repl()
        if len(files) not in (1, 2):
            return ParamsError('Bad Params for put command')
        src_file_name = os.path.abspath(files[0])
        dst_file_name = files[-1]
        if not dst_file_name.startswith('/'):
            dst_file_name = '/' + dst_file_name
        print(f"PUT: '{src_file_name}' to '{dst_file_name}'")
        with open(src_file_name, 'rb') as src_file:
            data = src_file.read()
            self._mpy.put(data, dst_file_name)

    def dump_log(self):
        try:
            while True:
                line = self._conn.read_line()
                line = line.decode('utf-8')
                print(line)
        except KeyboardInterrupt:
            return self._mpy.comm.enter_raw_repl()

    def process_commands(self, commands):
        try:
            while commands:
                command = commands.pop(0)
                if command == 'ls':
                    if commands:
                        self.ls(commands.pop(0))
                        break
                    self.ls('/')
                if command == 'tree':
                    if commands:
                        self.tree(commands.pop(0))
                        break
                    self.tree('/')
                elif command == 'get':
                    if commands:
                        self.get(*commands)
                        break
                    self.ls('/')
                elif command == 'put':
                    self.put(*commands)
                elif command == 'reset':
                    self._mpy.comm.soft_reset()
                elif command == 'dump_log':
                    self.dump_log()

        except CmdError as err:
            print(err)
        self._mpy.comm.exit_raw_repl()


class SimpleColorLogger():
    def __init__(self, loglevel=0):
        self._loglevel = loglevel

    def log(self, msg):
        print(msg, file=sys.stderr)

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
