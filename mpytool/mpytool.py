"""MicroPython tool"""

import os as _os
import sys as _sys
import argparse as _argparse
import mpytool as _mpytool
import mpytool.terminal as _terminal
import mpytool.__about__ as _about


class ParamsError(_mpytool.MpyError):
    """Timeout"""


class PathNotFound(_mpytool.MpyError):
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
        for path, _dirs, files in _os.walk(src_path):
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
                self._log.warning(' Exiting..')
            return

    def cmd_repl(self):
        self.verbose("REPL:")
        self._mpy.comm.exit_raw_repl()
        if not _terminal.AVAILABLE:
            self._log.error("REPL not available on this platform")
        print("Entering REPL mode, to exit press CTRL + ]")
        terminal = _terminal.Terminal()
        terminal.run(self._conn)
        if self._log:
            self._log.warning(' Exiting..')

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
                elif command in ('del', 'delete'):
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
                else:
                    raise ParamsError(f"unknown command: '{command}'")
        except _mpytool.MpyError as err:
            if self._log:
                self._log.error(err)
            else:
                print(err)
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


_VERSION_STR = "%s %s (%s <%s>)" % (
    _about.APP_NAME,
    _about.VERSION,
    _about.AUTHOR,
    _about.AUTHOR_EMAIL)
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
Aliases:
  dir                           alias to ls
  cat                           alias to get
  del                           alias to delete
"""


def main():
    """Main"""
    parser = _argparse.ArgumentParser(
        formatter_class=_argparse.RawTextHelpFormatter,
        epilog=_COMMANDS_HELP_STR)
    parser.add_argument(
        "-V", "--version", action='version', version=_VERSION_STR)
    parser.add_argument('-p', '--port', required=True, help="serial port")
    parser.add_argument(
        '-d', '--debug', default=0, action='count', help='set debug level')
    parser.add_argument(
        '-v', '--verbose', default=0, action='count', help='verbose output')
    parser.add_argument(
        "-e", "--exclude-dir", type=str, action='append', help='exclude dir, '
        'by default are excluded directories: __pycache__, .git, .svn')
    parser.add_argument('commands', nargs='*', help='commands')
    args = parser.parse_args()

    log = SimpleColorLogger(args.debug + 1)
    conn = _mpytool.ConnSerial(
        port=args.port, baudrate=115200, log=log)
    mpy_tool = MpyTool(
        conn, log=log, verbose=args.verbose, exclude_dirs=args.exclude_dir)
    mpy_tool.process_commands(args.commands)


if __name__ == '__main__':
    main()
