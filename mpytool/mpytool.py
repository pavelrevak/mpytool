"""MicroPython tool"""

import os as _os
import sys as _sys
import argparse as _argparse
import mpytool as _mpytool
import mpytool.terminal as _terminal
import mpytool.utils as _utils
import importlib.metadata as _metadata

try:
    _about = _metadata.metadata("mpytool")
except _metadata.PackageNotFoundError:
    _about = None


class ParamsError(_mpytool.MpyError):
    """Invalid command parameters"""


class MpyTool():
    SPACE = '   '
    BRANCH = '│  '
    TEE = '├─ '
    LAST = '└─ '

    def __init__(self, conn, log=None, verbose=0, exclude_dirs=None):
        self._conn = conn
        self._log = log
        self._verbose = verbose
        self._exclude_dirs = {'__pycache__', '.git', '.svn'}
        if exclude_dirs:
            self._exclude_dirs.update(exclude_dirs)
        self._mpy = _mpytool.Mpy(conn, log=log)

    def verbose(self, msg, level=1):
        if self._verbose >= level:
            print(msg, file=_sys.stderr)

    def cmd_ls(self, dir_name):
        result = self._mpy.ls(dir_name)
        for name, size in result:
            if size is not None:
                print(f'{size:8d} {name}')
            else:
                print(f'{"":8} {name}/')

    @classmethod
    def print_tree(cls, tree, prefix='', print_size=True, first=True, last=True):
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
            cls.print_tree(
                entry,
                prefix=prefix + sub_prefix,
                print_size=print_size,
                first=False,
                last=False)
        cls.print_tree(
            sub_tree[-1],
            prefix=prefix + sub_prefix,
            print_size=print_size,
            first=False,
            last=True)

    def cmd_tree(self, dir_name):
        tree = self._mpy.tree(dir_name)
        self.print_tree(tree)

    def cmd_get(self, *file_names):
        for file_name in file_names:
            self.verbose(f"GET: {file_name}")
            data = self._mpy.get(file_name)
            print(data.decode('utf-8'))

    def _put_dir(self, src_path, dst_path):
        basename = _os.path.basename(src_path)
        if basename:
            dst_path = _os.path.join(dst_path, basename)
        self.verbose(f"PUT_DIR: {src_path} -> {dst_path}")
        for path, dirs, files in _os.walk(src_path, topdown=True):
            dirs[:] = [d for d in dirs if d not in self._exclude_dirs]
            basename = _os.path.basename(path)
            if basename in self._exclude_dirs:
                continue
            rel_path = _os.path.relpath(path, src_path)
            if rel_path == '.':
                rel_path = ''
            rel_path = _os.path.join(dst_path, rel_path)
            if rel_path:
                self.verbose(f'mkdir: {rel_path}', 2)
                self._mpy.mkdir(rel_path)
            for file_name in files:
                spath = _os.path.join(path, file_name)
                dpath = _os.path.join(rel_path, file_name)
                self.verbose(f"  {dpath}")
                with open(spath, 'rb') as src_file:
                    data = src_file.read()
                    self._mpy.put(data, dpath)

    def _put_file(self, src_path, dst_path):
        basename = _os.path.basename(src_path)
        if basename and not _os.path.basename(dst_path):
            dst_path = _os.path.join(dst_path, basename)
        self.verbose(f"PUT_FILE: {src_path} -> {dst_path}")
        path = _os.path.dirname(dst_path)
        result = self._mpy.stat(path)
        if result is None:
            self._mpy.mkdir(path)
        elif result >= 0:
            raise _mpytool.MpyError(
                f'Error creating file under file: {path}')
        with open(src_path, 'rb') as src_file:
            data = src_file.read()
            self._mpy.put(data, dst_path)

    def cmd_put(self, src_path, dst_path):
        if _os.path.isdir(src_path):
            self._put_dir(src_path, dst_path)
        elif _os.path.isfile(src_path):
            self._put_file(src_path, dst_path)
        else:
            raise ParamsError(f'No file or directory to upload: {src_path}')

    def cmd_mkdir(self, *dir_names):
        for dir_name in dir_names:
            self.verbose(f"MKDIR: {dir_name}")
            self._mpy.mkdir(dir_name)

    def cmd_delete(self, *file_names):
        for file_name in file_names:
            self.verbose(f"DELETE: {file_name}")
            self._mpy.delete(file_name)

    def cmd_follow(self):
        self.verbose("FOLLOW:")
        try:
            while True:
                line = self._conn.read_line()
                line = line.decode('utf-8', 'backslashreplace')
                print(line)
        except KeyboardInterrupt:
            if self._log:
                self._log.info(' Exiting..')
        except _mpytool.ConnError as err:
            if self._log:
                self._log.error(err)

    def cmd_repl(self):
        self.verbose("REPL:")
        self._mpy.comm.exit_raw_repl()
        if not _terminal.AVAILABLE:
            self._log.error("REPL not available on this platform")
            return
        print("Entering REPL mode, to exit press CTRL + ]")
        terminal = _terminal.Terminal(self._conn, self._log)
        terminal.run()
        if self._log:
            self._log.info(' Exiting..')

    def cmd_exec(self, code):
        self.verbose(f"EXEC: {code}")
        result = self._mpy.comm.exec(code)
        if result:
            print(result.decode('utf-8', 'backslashreplace'), end='')

    def process_commands(self, commands):
        try:
            while commands:
                command = commands.pop(0)
                if command in ('ls', 'dir'):
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
                elif command in ('get', 'cat'):
                    if commands:
                        self.cmd_get(*commands)
                        break
                    raise ParamsError('missing file name for get command')
                elif command == 'put':
                    if commands:
                        src_path = commands.pop(0)
                        dst_path = ''
                        if commands:
                            dst_path = commands.pop(0)
                        self.cmd_put(src_path, dst_path)
                    else:
                        raise ParamsError('missing file name for put command')
                elif command == 'mkdir':
                    self.cmd_mkdir(*commands)
                    break
                elif command in ('del', 'delete', 'rm'):
                    self.cmd_delete(*commands)
                    break
                elif command == 'reset':
                    self._mpy.comm.soft_reset()
                elif command == 'follow':
                    self.cmd_follow()
                    break
                elif command == 'repl':
                    self.cmd_repl()
                    break
                elif command == 'exec':
                    if commands:
                        code = commands.pop(0)
                        self.cmd_exec(code)
                    else:
                        raise ParamsError('missing code for exec command')
                else:
                    raise ParamsError(f"unknown command: '{command}'")
        except (_mpytool.MpyError, _mpytool.ConnError) as err:
            if self._log:
                self._log.error(err)
            else:
                print(err)
        try:
            self._mpy.comm.exit_raw_repl()
        except _mpytool.ConnError:
            pass  # connection already lost


class SimpleColorLogger():
    # ANSI color codes
    _RESET = '\033[0m'
    _BOLD_RED = '\033[1;31m'
    _BOLD_YELLOW = '\033[1;33m'
    _BOLD_MAGENTA = '\033[1;35m'
    _BOLD_BLUE = '\033[1;34m'
    _CLEAR_LINE = '\033[K'

    # Progress bar characters
    _BAR_FILLED = '█'
    _BAR_EMPTY = '░'
    _BAR_WIDTH = 30

    def __init__(self, loglevel=1):
        self._loglevel = loglevel
        self._is_tty = (
            _sys.stderr.isatty()
            and _os.environ.get('NO_COLOR') is None
            and _os.environ.get('TERM') != 'dumb'
            and _os.environ.get('CI') is None
        )
        self._progress_shown = False

    def log(self, msg):
        if self._progress_shown:
            print(f'\r{self._CLEAR_LINE}', end='', file=_sys.stderr)
            self._progress_shown = False
        print(msg, file=_sys.stderr)

    def error(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 1:
            if self._is_tty:
                self.log(f"{self._BOLD_RED}{msg}{self._RESET}")
            else:
                self.log(f"E: {msg}")

    def warning(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 2:
            if self._is_tty:
                self.log(f"{self._BOLD_YELLOW}{msg}{self._RESET}")
            else:
                self.log(f"W: {msg}")

    def info(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 3:
            if self._is_tty:
                self.log(f"{self._BOLD_MAGENTA}{msg}{self._RESET}")
            else:
                self.log(f"I: {msg}")

    def debug(self, msg, *args):
        if args:
            msg = msg % args
        if self._loglevel >= 4:
            if self._is_tty:
                self.log(f"{self._BOLD_BLUE}{msg}{self._RESET}")
            else:
                self.log(f"D: {msg}")

    def progress(self, current, total, msg=''):
        """Show progress bar (only on TTY)"""
        if not self._is_tty or self._loglevel < 1:
            return
        percent = current * 100 // total if total > 0 else 0
        filled = self._BAR_WIDTH * current // total if total > 0 else 0
        bar = self._BAR_FILLED * filled + self._BAR_EMPTY * (self._BAR_WIDTH - filled)
        line = f"\r{bar} {percent:3d}% {msg}"
        print(line, end='', file=_sys.stderr, flush=True)
        self._progress_shown = True
        if current >= total:
            print(file=_sys.stderr)  # newline at end
            self._progress_shown = False


if _about:
    _VERSION_STR = "%s %s (%s)" % (_about["Name"], _about["Version"], _about["Author-email"])
else:
    _VERSION_STR = "mpytool (not installed version)"
_COMMANDS_HELP_STR = """
List of available commands:
  ls [{path}]                   list files and its sizes
  tree [{path}]                 list tree of structure and sizes
  get {path} [...]              get file and print it
  put {src_path} [{dst_path}]   put file or directory to destination
  mkdir {path} [...]            create directory (also create all parents)
  delete {path} [...]           remove file or directory (recursively)
  reset                         soft reset
  follow                        print log of running program
  repl                          enter REPL mode [UNIX OS ONLY]
  exec {code}                   execute Python code on device
Aliases:
  dir                           alias to ls
  cat                           alias to get
  del, rm                       alias to delete
Use -- to separate multiple commands:
  mpytool put main.py / -- reset -- follow
"""


def main():
    """Main"""
    _description = _about["Summary"] if _about else None
    parser = _argparse.ArgumentParser(
        description=_description,
        formatter_class=_argparse.RawTextHelpFormatter,
        epilog=_COMMANDS_HELP_STR)
    parser.add_argument(
        "-V", "--version", action='version', version=_VERSION_STR)
    parser.add_argument('-p', '--port', help="serial port")
    parser.add_argument('-a', '--address', help="network address")
    parser.add_argument('-b', '--baud', type=int, default=115200, help="serial port")
    parser.add_argument(
        '-d', '--debug', default=0, action='count', help='set debug level')
    parser.add_argument(
        '-v', '--verbose', default=0, action='count', help='verbose output')
    parser.add_argument(
        "-e", "--exclude-dir", type=str, action='append', help='exclude dir, '
        'by default are excluded directories: __pycache__, .git, .svn')
    parser.add_argument('commands', nargs=_argparse.REMAINDER, help='commands')
    args = parser.parse_args()

    log = SimpleColorLogger(args.debug + 1)
    if args.port and args.address:
        log.error("You can select only serial port or network address")
        return
    port = args.port
    if not port and not args.address:
        ports = _utils.detect_serial_ports()
        if not ports:
            log.error("No serial port found. Use -p to specify port.")
            return
        if len(ports) == 1:
            port = ports[0]
            if args.verbose:
                print(f"Using {port}", file=_sys.stderr)
        else:
            log.error("Multiple serial ports found: %s. Use -p to specify one.", ", ".join(ports))
            return
    try:
        if port:
            conn = _mpytool.ConnSerial(
                port=port, baudrate=args.baud, log=log)
        elif args.address:
            conn = _mpytool.ConnSocket(
                address=args.address, log=log)
    except _mpytool.ConnError as err:
        log.error(err)
        return
    mpy_tool = MpyTool(
        conn, log=log, verbose=args.verbose, exclude_dirs=args.exclude_dir)
    command_groups = _utils.split_commands(args.commands)
    for commands in command_groups:
        mpy_tool.process_commands(commands)


if __name__ == '__main__':
    main()
