"""MicroPython tool"""

import os as _os
import sys as _sys
import argparse as _argparse
import mpytool as _mpytool
import mpytool.terminal as _terminal
import mpytool.utils as _utils
from mpytool.logger import SimpleColorLogger
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

    def __init__(self, conn, log=None, verbose=None, exclude_dirs=None):
        self._conn = conn
        self._log = log if log is not None else SimpleColorLogger()
        self._verbose_out = verbose  # None = no verbose output (API mode)
        self._exclude_dirs = {'__pycache__', '.git', '.svn'}
        if exclude_dirs:
            self._exclude_dirs.update(exclude_dirs)
        self._mpy = _mpytool.Mpy(conn, log=self._log)
        # Progress tracking
        self._progress_total_files = 0
        self._progress_current_file = 0
        self._progress_src = ''
        self._progress_dst = ''
        self._progress_max_src_len = 0
        self._is_debug = getattr(self._log, '_loglevel', 1) >= 4
        self._batch_mode = False

    @property
    def _is_tty(self):
        if self._verbose_out is None:
            return False
        return self._verbose_out._is_tty

    @property
    def _verbose(self):
        if self._verbose_out is None:
            return 0
        return self._verbose_out._verbose_level

    def verbose(self, msg, level=1, color='green', end='\n', overwrite=False):
        if self._verbose_out is not None:
            self._verbose_out.verbose(msg, level, color, end, overwrite)

    @staticmethod
    def _format_local_path(path):
        """Format local path: relative from CWD, absolute if > 2 levels up"""
        try:
            rel_path = _os.path.relpath(path)
            # Count leading ../
            parts = rel_path.split(_os.sep)
            up_count = 0
            for part in parts:
                if part == '..':
                    up_count += 1
                else:
                    break
            if up_count > 2:
                return _os.path.abspath(path)
            return rel_path
        except ValueError:
            # On Windows, relpath fails for different drives
            return _os.path.abspath(path)

    def _format_progress_line(self, percent, total):
        """Format progress line: [2/5]  23% 24.1KB source -> dest"""
        size_str = self.format_size(total).replace(' ', '')
        if self._progress_total_files > 1:
            prefix = f"[{self._progress_current_file}/{self._progress_total_files}]"
        else:
            prefix = ""
        # Pad source to align ->
        src_width = max(len(self._progress_src), self._progress_max_src_len)
        return f"{prefix:>7} {percent:3d}% {size_str:>7} {self._progress_src:<{src_width}} -> {self._progress_dst}"

    def _progress_callback(self, transferred, total):
        """Callback for file transfer progress"""
        percent = (transferred * 100 // total) if total > 0 else 100
        line = self._format_progress_line(percent, total)
        if self._is_debug:
            # Debug mode: always newlines
            self.verbose(line, color='cyan')
        else:
            # Normal mode: overwrite line
            self.verbose(line, color='cyan', end='', overwrite=True)

    def _progress_complete(self, total):
        """Mark current file as complete"""
        line = self._format_progress_line(100, total)
        if self._is_debug:
            # Already printed with newline in callback
            pass
        else:
            # Print final line with newline
            self.verbose(line, color='cyan', overwrite=True)

    def _set_progress_info(self, src, dst, is_src_remote, is_dst_remote):
        """Set progress source and destination paths"""
        if is_src_remote:
            self._progress_src = ':' + src
        else:
            self._progress_src = self._format_local_path(src)
        if is_dst_remote:
            self._progress_dst = ':' + dst
        else:
            self._progress_dst = self._format_local_path(dst)

    def _count_local_files(self, path):
        """Count files in local directory (excluding excluded dirs)"""
        if _os.path.isfile(path):
            return 1
        count = 0
        for _, dirs, files in _os.walk(path, topdown=True):
            dirs[:] = [d for d in dirs if d not in self._exclude_dirs]
            count += len(files)
        return count

    def _count_remote_files(self, path):
        """Count files on device"""
        stat = self._mpy.stat(path)
        if stat is None:
            return 0
        if stat >= 0:  # file
            return 1
        # directory - count recursively
        count = 0
        entries = self._mpy.ls(path)
        for name, size in entries:
            if size is None:  # directory
                entry_path = path.rstrip('/') + '/' + name
                count += self._count_remote_files(entry_path)
            else:  # file
                count += 1
        return count

    def _collect_source_paths(self, commands):
        """Collect source paths for a cp/put command (for alignment calculation)"""
        paths = []
        if not commands:
            return paths
        cmd = commands[0]
        if cmd == 'cp' and len(commands) >= 3:
            sources = commands[1:-1]
            for src in sources:
                src_is_remote = src.startswith(':')
                src_path = src[1:] if src_is_remote else src
                if not src_path:
                    src_path = '/'
                src_path = src_path.rstrip('/') or '/'
                if src_is_remote:
                    # Collect all file paths from remote
                    paths.extend(self._collect_remote_paths(src_path))
                else:
                    # Collect all file paths from local
                    paths.extend(self._collect_local_paths(src_path))
        elif cmd == 'put' and len(commands) >= 2:
            src_path = commands[1]
            paths.extend(self._collect_local_paths(src_path))
        return paths

    def _collect_local_paths(self, path):
        """Collect all local file paths (formatted for display)"""
        paths = []
        if _os.path.isfile(path):
            paths.append(self._format_local_path(path))
        elif _os.path.isdir(path):
            for root, dirs, files in _os.walk(path, topdown=True):
                dirs[:] = [d for d in dirs if d not in self._exclude_dirs]
                for f in files:
                    full_path = _os.path.join(root, f)
                    paths.append(self._format_local_path(full_path))
        return paths

    def _collect_remote_paths(self, path):
        """Collect all remote file paths (formatted for display)"""
        paths = []
        stat = self._mpy.stat(path)
        if stat is None:
            return paths
        if stat >= 0:  # file
            paths.append(':' + path)
        else:  # directory
            entries = self._mpy.ls(path)
            for name, size in entries:
                entry_path = path.rstrip('/') + '/' + name
                if size is None:  # directory
                    paths.extend(self._collect_remote_paths(entry_path))
                else:  # file
                    paths.append(':' + entry_path)
        return paths

    def count_files_for_command(self, commands):
        """Count files that would be transferred by cp/put command.
        Returns (is_copy_command, file_count, source_paths)"""
        paths = self._collect_source_paths(commands)
        if paths:
            return True, len(paths), paths
        return False, 0, []

    def set_batch_progress(self, total_files, max_src_len=0):
        """Set batch progress for consecutive copy commands"""
        self._progress_total_files = total_files
        self._progress_current_file = 0
        self._progress_max_src_len = max_src_len
        self._batch_mode = True

    def reset_batch_progress(self):
        """Reset batch progress mode"""
        self._batch_mode = False
        self._progress_total_files = 0
        self._progress_current_file = 0
        self._progress_max_src_len = 0

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
            self.verbose(f"GET: {file_name}", 2)
            data = self._mpy.get(file_name)
            print(data.decode('utf-8'))

    def _put_dir(self, src_path, dst_path, show_progress=True):
        basename = _os.path.basename(src_path)
        if basename:
            dst_path = _os.path.join(dst_path, basename)
        self.verbose(f"PUT DIR: {src_path} -> {dst_path}", 2)
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
                self.verbose(f'MKDIR: {rel_path}', 2)
                self._mpy.mkdir(rel_path)
            for file_name in files:
                spath = _os.path.join(path, file_name)
                dpath = _os.path.join(rel_path, file_name)
                with open(spath, 'rb') as src_file:
                    data = src_file.read()
                    if show_progress and self._verbose >= 1:
                        self._progress_current_file += 1
                        self._set_progress_info(spath, dpath, False, True)
                        self._mpy.put(data, dpath, self._progress_callback)
                        self._progress_complete(len(data))
                    else:
                        self._mpy.put(data, dpath)

    def _put_file(self, src_path, dst_path, show_progress=True):
        basename = _os.path.basename(src_path)
        if basename and not _os.path.basename(dst_path):
            dst_path = _os.path.join(dst_path, basename)
        self.verbose(f"PUT FILE: {src_path} -> {dst_path}", 2)
        path = _os.path.dirname(dst_path)
        result = self._mpy.stat(path)
        if result is None:
            self._mpy.mkdir(path)
        elif result >= 0:
            raise _mpytool.MpyError(
                f'Error creating file under file: {path}')
        with open(src_path, 'rb') as src_file:
            data = src_file.read()
            if show_progress and self._verbose >= 1:
                self._progress_current_file += 1
                self._set_progress_info(src_path, dst_path, False, True)
                self._mpy.put(data, dst_path, self._progress_callback)
                self._progress_complete(len(data))
            else:
                self._mpy.put(data, dst_path)

    def cmd_put(self, src_path, dst_path):
        if self._verbose >= 1 and not self._batch_mode:
            self._progress_total_files = self._count_local_files(src_path)
            self._progress_current_file = 0
        if _os.path.isdir(src_path):
            self._put_dir(src_path, dst_path)
        elif _os.path.isfile(src_path):
            self._put_file(src_path, dst_path)
        else:
            raise ParamsError(f'No file or directory to upload: {src_path}')

    def _get_file(self, src_path, dst_path, show_progress=True):
        """Download single file from device"""
        self.verbose(f"GET FILE: {src_path} -> {dst_path}", 2)
        # Create destination directory if needed
        dst_dir = _os.path.dirname(dst_path)
        if dst_dir and not _os.path.exists(dst_dir):
            _os.makedirs(dst_dir)
        if show_progress and self._verbose >= 1:
            self._progress_current_file += 1
            self._set_progress_info(src_path, dst_path, True, False)
            data = self._mpy.get(src_path, self._progress_callback)
            self._progress_complete(len(data))
        else:
            data = self._mpy.get(src_path)
        with open(dst_path, 'wb') as dst_file:
            dst_file.write(data)

    def _get_dir(self, src_path, dst_path, copy_contents=False, show_progress=True):
        """Download directory from device"""
        if not copy_contents:
            basename = src_path.rstrip('/').split('/')[-1]
            if basename:
                dst_path = _os.path.join(dst_path, basename)
        self.verbose(f"GET DIR: {src_path} -> {dst_path}", 2)
        if not _os.path.exists(dst_path):
            _os.makedirs(dst_path)
        entries = self._mpy.ls(src_path)
        for name, size in entries:
            src_entry = src_path.rstrip('/') + '/' + name
            dst_entry = _os.path.join(dst_path, name)
            if size is None:  # directory
                self._get_dir(src_entry, dst_entry, copy_contents=True, show_progress=show_progress)
            else:  # file
                self._get_file(src_entry, dst_entry, show_progress=show_progress)

    def _cp_local_to_remote(self, src_path, dst_path, dst_is_dir):
        """Upload local file/dir to device"""
        src_is_dir = _os.path.isdir(src_path)
        copy_contents = src_path.endswith('/')
        src_path = src_path.rstrip('/')
        if not _os.path.exists(src_path):
            raise ParamsError(f'Source not found: {src_path}')
        if dst_is_dir:
            if not copy_contents:
                basename = _os.path.basename(src_path)
                dst_path = dst_path + basename
            dst_path = dst_path.rstrip('/')
        if src_is_dir:
            if copy_contents:
                # Copy contents of directory
                for item in _os.listdir(src_path):
                    item_src = _os.path.join(src_path, item)
                    if _os.path.isdir(item_src):
                        self._put_dir(item_src, dst_path)
                    else:
                        self._put_file(item_src, dst_path + '/')
            else:
                self._put_dir(src_path, _os.path.dirname(dst_path) or '/')
        else:
            self._put_file(src_path, dst_path)

    def _cp_remote_to_local(self, src_path, dst_path, dst_is_dir):
        """Download file/dir from device to local"""
        copy_contents = src_path.endswith('/')
        src_path = src_path.rstrip('/') or '/'
        stat = self._mpy.stat(src_path)
        if stat is None:
            raise ParamsError(f'Source not found on device: {src_path}')
        src_is_dir = (stat == -1)
        if dst_is_dir:
            if not _os.path.exists(dst_path):
                _os.makedirs(dst_path)
            if not copy_contents and src_path != '/':
                basename = src_path.split('/')[-1]
                dst_path = _os.path.join(dst_path, basename)
        if src_is_dir:
            self._get_dir(src_path, dst_path, copy_contents=copy_contents)
        else:
            if _os.path.isdir(dst_path):
                basename = src_path.split('/')[-1]
                dst_path = _os.path.join(dst_path, basename)
            self._get_file(src_path, dst_path)

    def _cp_remote_to_remote(self, src_path, dst_path, dst_is_dir):
        """Copy file on device"""
        src_path = src_path.rstrip('/') or '/'
        stat = self._mpy.stat(src_path)
        if stat is None:
            raise ParamsError(f'Source not found on device: {src_path}')
        if stat == -1:
            raise ParamsError('Remote-to-remote directory copy not supported yet')
        # File copy on device
        if dst_is_dir:
            basename = src_path.split('/')[-1]
            dst_path = dst_path + basename
        self.verbose(f"COPY: {src_path} -> {dst_path}", 2)
        if self._verbose >= 1:
            self._progress_current_file += 1
            self._set_progress_info(src_path, dst_path, True, True)
            data = self._mpy.get(src_path, self._progress_callback)
            self._mpy.put(data, dst_path)
            self._progress_complete(len(data))
        else:
            data = self._mpy.get(src_path)
            self._mpy.put(data, dst_path)

    def cmd_cp(self, *args):
        """Copy files between local and device"""
        if len(args) < 2:
            raise ParamsError('cp requires source and destination')
        sources = list(args[:-1])
        dest = args[-1]
        dest_is_remote = dest.startswith(':')
        dest_path = dest[1:] if dest_is_remote else dest
        if not dest_path:
            dest_path = '/'
        dest_is_dir = dest_path.endswith('/')
        if len(sources) > 1 and not dest_is_dir:
            raise ParamsError('multiple sources require destination directory (ending with /)')
        # Count total files for progress (only if not in batch mode)
        if self._verbose >= 1 and not self._batch_mode:
            total_files = 0
            for src in sources:
                src_is_remote = src.startswith(':')
                src_path = src[1:] if src_is_remote else src
                if not src_path:
                    src_path = '/'
                src_path_clean = src_path.rstrip('/') or '/'
                if src_is_remote:
                    total_files += self._count_remote_files(src_path_clean)
                else:
                    total_files += self._count_local_files(src_path_clean)
            self._progress_total_files = total_files
            self._progress_current_file = 0
        for src in sources:
            src_is_remote = src.startswith(':')
            src_path = src[1:] if src_is_remote else src
            if not src_path:
                src_path = '/'
            if src_is_remote and dest_is_remote:
                self._cp_remote_to_remote(src_path, dest_path, dest_is_dir)
            elif src_is_remote:
                self._cp_remote_to_local(src_path, dest_path, dest_is_dir)
            elif dest_is_remote:
                self._cp_local_to_remote(src_path, dest_path, dest_is_dir)
            else:
                self.verbose(f"skip local-to-local: {src} -> {dest}", 2)

    def cmd_mv(self, *args):
        """Move/rename files on device"""
        if len(args) < 2:
            raise ParamsError('mv requires source and destination')
        sources = list(args[:-1])
        dest = args[-1]
        # Validate all paths are remote
        if not dest.startswith(':'):
            raise ParamsError('mv destination must be device path (: prefix)')
        for src in sources:
            if not src.startswith(':'):
                raise ParamsError('mv source must be device path (: prefix)')
        dest_path = dest[1:] or '/'
        dest_is_dir = dest_path.endswith('/')
        if len(sources) > 1 and not dest_is_dir:
            raise ParamsError('multiple sources require destination directory (ending with /)')
        self._mpy.import_module('os')
        for src in sources:
            src_path = src[1:]
            stat = self._mpy.stat(src_path)
            if stat is None:
                raise ParamsError(f'Source not found on device: {src_path}')
            if dest_is_dir:
                # Ensure destination directory exists
                dst_dir = dest_path.rstrip('/')
                if dst_dir and self._mpy.stat(dst_dir) is None:
                    self._mpy.mkdir(dst_dir)
                basename = src_path.rstrip('/').split('/')[-1]
                final_dest = dest_path + basename
            else:
                final_dest = dest_path
            self.verbose(f"MV: {src_path} -> {final_dest}", 1)
            self._mpy.rename(src_path, final_dest)

    def cmd_mkdir(self, *dir_names):
        for dir_name in dir_names:
            self.verbose(f"MKDIR: {dir_name}", 1)
            self._mpy.mkdir(dir_name)

    def cmd_delete(self, *file_names):
        for file_name in file_names:
            contents_only = file_name.endswith('/')
            path = file_name.rstrip('/') or '/'
            if contents_only:
                self.verbose(f"DELETE contents: {path}", 1)
                entries = self._mpy.ls(path)
                for name, size in entries:
                    entry_path = path + '/' + name if path != '/' else '/' + name
                    self.verbose(f"  {entry_path}", 1)
                    self._mpy.delete(entry_path)
            else:
                self.verbose(f"DELETE: {path}", 1)
                self._mpy.delete(path)

    def cmd_follow(self):
        self.verbose("FOLLOW (Ctrl+C to stop)", 1)
        try:
            while True:
                line = self._conn.read_line()
                line = line.decode('utf-8', 'backslashreplace')
                print(line)
        except KeyboardInterrupt:
            self.verbose('', level=0, overwrite=True)  # newline after ^C
        except _mpytool.ConnError as err:
            if self._log:
                self._log.error(err)

    def cmd_repl(self):
        self._mpy.comm.exit_raw_repl()
        if not _terminal.AVAILABLE:
            self._log.error("REPL not available on this platform")
            return
        self.verbose("REPL (Ctrl+] to exit)", 1)
        terminal = _terminal.Terminal(self._conn, self._log)
        terminal.run()
        self._log.info('Exiting..')

    def cmd_exec(self, code):
        self.verbose(f"EXEC: {code}", 1)
        result = self._mpy.comm.exec(code)
        if result:
            print(result.decode('utf-8', 'backslashreplace'), end='')

    @staticmethod
    def format_size(size):
        """Format size in bytes to human readable format with 3+ digits"""
        if size < 1000:
            return f"{size} B"
        for unit in ('KB', 'MB', 'GB', 'TB'):
            size /= 1024
            if size < 10:
                return f"{size:.2f} {unit}"
            if size < 100:
                return f"{size:.1f} {unit}"
            if size < 1000 or unit == 'TB':
                return f"{size:.0f} {unit}"
        return f"{size:.0f} TB"

    def cmd_info(self):
        self.verbose("INFO", 2)
        self._mpy.comm.exec("import sys, gc, os")
        platform = self._mpy.comm.exec_eval("repr(sys.platform)")
        version = self._mpy.comm.exec_eval("repr(sys.version)")
        impl = self._mpy.comm.exec_eval("repr(sys.implementation.name)")
        gc_free = self._mpy.comm.exec_eval("gc.mem_free()")
        gc_alloc = self._mpy.comm.exec_eval("gc.mem_alloc()")
        gc_total = gc_free + gc_alloc
        gc_pct = (gc_alloc / gc_total * 100) if gc_total > 0 else 0
        try:
            uname = self._mpy.comm.exec_eval("tuple(os.uname())")
            machine = uname[4] if len(uname) > 4 else None
        except _mpytool.MpyError:
            machine = None
        # Collect filesystem info - root and any different mount points
        fs_info = []
        try:
            fs_stat = self._mpy.comm.exec_eval("os.statvfs('/')")
            fs_total = fs_stat[0] * fs_stat[2]
            fs_free = fs_stat[0] * fs_stat[3]
            if fs_total > 0:
                fs_info.append({
                    'mount': '/', 'total': fs_total,
                    'used': fs_total - fs_free,
                    'pct': ((fs_total - fs_free) / fs_total * 100)
                })
        except _mpytool.MpyError:
            pass
        # Check subdirectories for additional mount points
        try:
            root_dirs = self._mpy.comm.exec_eval("[d[0] for d in os.ilistdir('/') if d[1] == 0x4000]")
            for dirname in root_dirs:
                try:
                    path = '/' + dirname
                    sub_stat = self._mpy.comm.exec_eval(f"os.statvfs('{path}')")
                    sub_total = sub_stat[0] * sub_stat[2]
                    sub_free = sub_stat[0] * sub_stat[3]
                    # Skip if same as root or zero size
                    if sub_total == 0 or any(f['total'] == sub_total for f in fs_info):
                        continue
                    fs_info.append({
                        'mount': path, 'total': sub_total,
                        'used': sub_total - sub_free,
                        'pct': ((sub_total - sub_free) / sub_total * 100)
                    })
                except _mpytool.MpyError:
                    pass
        except _mpytool.MpyError:
            pass
        print(f"Platform:    {platform}")
        print(f"Version:     {version}")
        print(f"Impl:        {impl}")
        if machine:
            print(f"Machine:     {machine}")
        print(f"Memory:      {self.format_size(gc_alloc)} / {self.format_size(gc_total)} ({gc_pct:.2f}%)")
        for fs in fs_info:
            label = "Flash:" if fs['mount'] == '/' else fs['mount'] + ':'
            print(f"{label:12} {self.format_size(fs['used'])} / {self.format_size(fs['total'])} ({fs['pct']:.2f}%)")

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
                    self.verbose("RESET", 1)
                    self._mpy.comm.soft_reset()
                    self._mpy.reset_state()
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
                elif command == 'info':
                    self.cmd_info()
                elif command == 'cp':
                    if len(commands) >= 2:
                        self.cmd_cp(*commands)
                        break
                    raise ParamsError('cp requires source and destination')
                elif command == 'mv':
                    if len(commands) >= 2:
                        self.cmd_mv(*commands)
                        break
                    raise ParamsError('mv requires source and destination')
                else:
                    raise ParamsError(f"unknown command: '{command}'")
        except (_mpytool.MpyError, _mpytool.ConnError) as err:
            self._log.error(err)
        try:
            self._mpy.comm.exit_raw_repl()
        except _mpytool.ConnError:
            pass  # connection already lost


if _about:
    _VERSION_STR = "%s %s (%s)" % (_about["Name"], _about["Version"], _about["Author-email"])
else:
    _VERSION_STR = "mpytool (not installed version)"
_COMMANDS_HELP_STR = """
List of available commands:
  ls [{path}]                   list files and its sizes
  tree [{path}]                 list tree of structure and sizes
  cp {src} [...] {dst}          copy files (: prefix = device path)
  mv {src} [...] {dst}          move/rename on device (: prefix required)
  get {path} [...]              get file and print it
  put {src_path} [{dst_path}]   put file or directory to destination
  mkdir {path} [...]            create directory (also create all parents)
  delete {path} [...]           remove file/dir (path/ = contents only)
  reset                         soft reset
  follow                        print log of running program
  repl                          enter REPL mode [UNIX OS ONLY]
  exec {code}                   execute Python code on device
  info                          show device information
Aliases:
  dir                           alias to ls
  cat                           alias to get
  del, rm                       alias to delete
Use -- to separate multiple commands:
  mpytool put main.py / -- reset -- follow
"""


def _run_commands(mpy_tool, command_groups, with_progress=True):
    """Execute command groups with optional batch progress tracking"""
    if not with_progress:
        for commands in command_groups:
            mpy_tool.process_commands(commands)
        return
    # Pre-scan to identify consecutive copy command batches (for progress)
    i = 0
    while i < len(command_groups):
        is_copy, count, paths = mpy_tool.count_files_for_command(command_groups[i])
        if not is_copy:
            mpy_tool.process_commands(command_groups[i])
            i += 1
            continue
        # Collect consecutive copy commands into a batch
        batch_total = count
        all_paths = paths
        batch_start = i
        j = i + 1
        while j < len(command_groups):
            is_copy_j, count_j, paths_j = mpy_tool.count_files_for_command(command_groups[j])
            if not is_copy_j:
                break
            batch_total += count_j
            all_paths.extend(paths_j)
            j += 1
        # Execute batch with combined count
        max_src_len = max(len(p) for p in all_paths) if all_paths else 0
        mpy_tool.verbose("COPY", 1)
        mpy_tool.set_batch_progress(batch_total, max_src_len)
        for k in range(batch_start, j):
            mpy_tool.process_commands(command_groups[k])
        mpy_tool.reset_batch_progress()
        i = j


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
        '-v', '--verbose', action='store_true', help='verbose output (show commands)')
    parser.add_argument(
        '-q', '--quiet', action='store_true', help='quiet mode (no progress)')
    parser.add_argument(
        "-e", "--exclude-dir", type=str, action='append', help='exclude dir, '
        'by default are excluded directories: __pycache__, .git, .svn')
    parser.add_argument('commands', nargs=_argparse.REMAINDER, help='commands')
    args = parser.parse_args()
    # Convert to numeric level: 0=quiet, 1=progress, 2=verbose
    if args.quiet:
        args.verbose = 0
    elif args.verbose:
        args.verbose = 2
    else:
        args.verbose = 1

    log = SimpleColorLogger(args.debug + 1, verbose_level=args.verbose)
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
            log.verbose(f"Using {port}", level=1)
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
    mpy_tool = MpyTool(conn, log=log, verbose=log, exclude_dirs=args.exclude_dir)
    command_groups = _utils.split_commands(args.commands)
    try:
        _run_commands(mpy_tool, command_groups, with_progress=(args.verbose >= 1))
    except KeyboardInterrupt:
        # Clear partial progress line and show clean message
        log.verbose('Interrupted', level=0, overwrite=True)


if __name__ == '__main__':
    main()
