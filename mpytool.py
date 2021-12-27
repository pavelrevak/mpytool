import sys
import time
import argparse as _argparse
import serial as _serial


class Timeout(Exception):
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


class MpyTool():
    def __init__(self, loglevel=3, **serial_config):
        self._loglevel = loglevel
        self._serial = _serial.Serial(**serial_config)
        self._imported = []

    def log(self, msg):
        print(msg, file=sys.stderr)

    def log_err(self, msg):
        if self._loglevel >= 1:
            self.log(f"\033[1;31m{msg}\033[0m")

    def log_warn(self, msg):
        if self._loglevel >= 2:
            self.log(f"\033[1;33m{msg}\033[0m")

    def log_info(self, msg):
        if self._loglevel >= 3:
            self.log(f"\033[1;35m{msg}\033[0m")

    def log_dbg(self, msg):
        if self._loglevel >= 4:
            self.log(f"\033[1;34m{msg}\033[0m")

    def write(self, data):
        self.log_dbg(f"wr: {data}")
        self._serial.write(data)

    def read_until(self, end, timeout=1):
        data = b''
        max_time = time.time() + timeout
        while not data.endswith(end):
            in_waiting = self._serial.in_waiting
            if in_waiting > 0:
                max_time = time.time() + timeout
                data += self._serial.read()
            elif max_time < time.time():
                if data:
                    self.log_warn("tout: {data}")
                    raise Timeout(f"During timeout received: {data}")
                self.log_warn("rd: timeout")
                raise Timeout("No data received")
            else:
                time.sleep(.01)
        self.log_dbg(f"rd: {data}")
        data = data.rstrip(end)
        return data

    def enter_raw_repl(self):
        self.log_info('ENTER RAW REPL')
        # stop current operations (in case is app running)
        self.write(b'\x03')
        try:
            # wait for prompt
            self.read_until(b'\r\n>>> ', timeout=.2)
        except Timeout:
            # probably is in RAW repl
            self.exit_raw_repl()
        # enter raw repl
        self.write(b'\x01')
        self.read_until(b'\r\n>')

    def exit_raw_repl(self):
        self.log_info('EXIT RAW REPL')
        self.write(b'\x02')
        self.read_until(b'\r\n>>> ')

    def run_command(self, cmd):
        # wait for prompt
        self.log_info(f"CMD: {cmd}")
        self.write(bytes(cmd, 'utf-8'))
        self.write(b'\x04')
        self.read_until(b'OK')
        result = self.read_until(b'\x04')
        if result:
            self.log_info(f'RES: {result}')
        err = self.read_until(b'\x04>')
        if err:
            raise CmdError(cmd, result, err)
        return result

    def import_module(self, module):
        if module not in self._imported:
            self.run_command(f'import {module}')
            self._imported.append(module)

    def ls(self, *dir_names):
        first = True
        for dir_name in dir_names:
            if not dir_name.startswith('/'):
                dir_name = '/' + dir_name
            if not dir_name.endswith('/'):
                dir_name = dir_name + '/'
            if len(dir_names) > 1:
                if not first:
                    print()
                print(f"{dir_name}:")
            self.import_module('os')
            result = self.run_command(f"print(os.listdir('{dir_name}'))")
            result_eval = eval(result)
            for i in result_eval:
                print(f"{i}")
            first = False

    def get(self, *file_names):
        first = True
        for file_name in file_names:
            if not file_name.startswith('/'):
                file_name = '/' + file_name
            if len(file_names) > 1:
                if not first:
                    print()
                print(f"{file_name}:")
            result = self.run_command(f"f = open('{file_name}', 'rb')")
            data = b''
            while True:
                result = self.run_command(f"print(f.read(256))")
                result_eval = eval(result)
                if not result_eval:
                    break
                data += result_eval
            result = self.run_command(f"f.close()")
            print(data.decode('utf-8'))
            first = False

    def process_commands(self, commands):
        try:
            while commands:
                command = commands.pop(0)
                if command == 'ls':
                    if commands:
                        self.ls(*commands)
                        break
                    self.ls('/')
                elif command == 'get':
                    if commands:
                        self.get(*commands)
                        break
                    self.ls('/')
        except CmdError as err:
            self.log_err(err)


def main():
    parser = _argparse.ArgumentParser()
    parser.add_argument('-p', '--port', required=True, help="serial port")
    parser.add_argument('-v', '--verbose', default=0, action='count', help='increase verbosity')
    parser.add_argument('commands', nargs='*', help='commands')
    args = parser.parse_args()
    mpy = MpyTool(port=args.port, baudrate=115200, loglevel=args.verbose + 1)
    mpy.enter_raw_repl()
    mpy.process_commands(args.commands)
    mpy.exit_raw_repl()


if __name__ == '__main__':
    main()
