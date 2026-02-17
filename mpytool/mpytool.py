"""MicroPython tool"""

import argparse as _argparse
import fnmatch as _fnmatch
import hashlib as _hashlib
import importlib.metadata as _metadata
import os as _os
import sys as _sys
import time as _time

import mpytool as _mpytool
import mpytool.terminal as _terminal
import mpytool.utils as _utils
from mpytool.logger import SimpleColorLogger
from mpytool.mpy_cross import MpyCross

try:
    _about = _metadata.metadata("mpytool")
except _metadata.PackageNotFoundError:
    _about = None

if _about:
    _VERSION_STR = "%s %s (%s)" % (
        _about["Name"], _about["Version"], _about["Author-email"])
else:
    _VERSION_STR = "mpytool (not installed version)"

# Order of commands in help and completion
_CMD_ORDER = [
    'ls', 'tree', 'cat', 'cp', 'mv', 'mkdir', 'rm', 'pwd', 'cd', 'path',
    'stop', 'reset', 'monitor', 'repl', 'exec', 'run', 'info',
    'flash', 'ota', 'mount', 'ln', 'speedtest', 'sleep',
]


class ParamsError(_mpytool.MpyError):
    """Invalid command parameters"""


def _join_remote_path(base, name):
    """Join remote path components (handles empty string and '/' correctly)"""
    if not name:
        return base
    if base == '/':
        return '/' + name
    elif base:
        return base.rstrip('/') + '/' + name
    else:
        return name


def _remote_basename(path):
    """Get basename from remote path"""
    return path.rstrip('/').split('/')[-1]


def _parse_device_path(path, cmd_name):
    """Parse device path with : prefix, raise ParamsError if missing

    Args:
        path: path string (should start with :)
        cmd_name: command name for error message

    Returns:
        path without : prefix
    """
    if not path.startswith(':'):
        raise ParamsError(
            f'{cmd_name} requires device path (: prefix): {path}')
    return path[1:]


# Command argument subparsers
class _CmdParser(_argparse.ArgumentParser):
    """ArgumentParser that raises ParamsError instead of sys.exit()"""

    def exit(self, status=0, message=None):
        # Allow normal exit for --help (status=0)
        if status == 0:
            raise SystemExit(status)
        # For errors, raise ParamsError instead
        raise ParamsError(message.strip() if message else f'{self.prog}: error')

    def error(self, message):
        raise ParamsError(f'{self.prog}: {message}')


def _build_subparsers():
    # cp: copy files
    cp = _CmdParser(
        prog='cp',
        description='Copy files between local and device.')
    cp.add_argument('-f', '--force', action='store_true',
        help='overwrite without checking')
    cp.add_argument('-m', '--mpy', action='store_true',
        help='compile .py to .mpy')
    cp.add_argument('-z', '--compress', action='store_true',
        help='force compression')
    cp.add_argument(
        '-Z', '--no-compress', dest='no_compress', action='store_true',
        help='disable compression')
    cp.add_argument(
        'paths', nargs='*', metavar='local_or_remote',
        help='source(s) and dest (: prefix for device)')

    # reset: device reset
    reset = _CmdParser(
        prog='reset',
        description='Reset the device. Default: soft reset (Ctrl-D).')
    reset_mode = reset.add_mutually_exclusive_group()
    reset_mode.add_argument('--machine', action='store_const', const='machine',
        dest='mode', help='machine.reset() with reconnect')
    reset_mode.add_argument('--rts', action='store_const', const='rts',
        dest='mode', help='hardware reset via DTR/RTS')
    reset_mode.add_argument('--raw', action='store_const', const='raw',
        dest='mode', help='soft reset in raw REPL')
    reset_mode.add_argument('--boot', action='store_const', const='boot',
        dest='mode', help='enter bootloader')
    reset_mode.add_argument(
        '--dtr-boot', action='store_const', const='dtr-boot',
        dest='mode', help='bootloader via DTR/RTS (ESP32)')
    reset.add_argument('-t', '--timeout', type=int,
        help='reconnect timeout in seconds')

    # mount: mount local directory
    mount = _CmdParser(
        prog='mount',
        description='Mount local dir as VFS. Without args, list mounts.')
    mount.add_argument('-m', '--mpy', action='store_true',
        help='compile .py to .mpy on-the-fly')
    mount.add_argument('-w', '--writable', '--write', action='store_true',
        help='mount as writable')
    mount.add_argument(
        'paths', nargs='*', metavar='local_and_remote',
        help='local_dir [:mount_point] (default: /remote)')

    # path: manage sys.path
    path = _CmdParser(
        prog='path',
        description='Manage sys.path. Without args, show current path.')
    path_mode = path.add_mutually_exclusive_group()
    path_mode.add_argument(
        '-f', '--first', action='store_const', const='first',
        dest='mode', help='prepend to sys.path')
    path_mode.add_argument(
        '-a', '--append', action='store_const', const='append',
        dest='mode', help='append to sys.path')
    path_mode.add_argument(
        '-d', '--delete', action='store_const', const='delete',
        dest='mode', help='delete from sys.path')
    path.add_argument('paths', nargs='*', metavar='remote',
        help='paths to add/remove (: prefix required)')

    # ls: list files
    ls = _CmdParser(prog='ls',
        description='List files and directories on device.')
    ls.add_argument('path', nargs='?', default=':', metavar='remote',
        help='device path (default: CWD)')

    # tree: directory tree
    tree = _CmdParser(prog='tree',
        description='Show directory tree on device.')
    tree.add_argument('path', nargs='?', default=':', metavar='remote',
        help='device path (default: CWD)')

    # cat: print file content
    cat = _CmdParser(prog='cat',
        description='Print file content from device to stdout.')
    cat.add_argument('paths', nargs='+', metavar='remote',
        help='device path(s) to print')

    # mkdir: create directory
    mkdir = _CmdParser(
        prog='mkdir',
        description='Create directory (with parents if needed).')
    mkdir.add_argument('paths', nargs='+', metavar='remote',
        help='device path(s) to create')

    # rm: delete files/directories
    rm = _CmdParser(
        prog='rm',
        description='Delete files/dirs. Use :path/ for contents only.')
    rm.add_argument('paths', nargs='+', metavar='remote',
        help='device path(s) to delete')

    # cd: change directory
    cd = _CmdParser(prog='cd',
        description='Change current working directory on device.')
    cd.add_argument('path', metavar='remote', help='device path')

    # mv: move/rename files
    mv = _CmdParser(prog='mv',
        description='Move or rename files on device.')
    mv.add_argument('paths', nargs='+', metavar='remote',
        help='source(s) and destination (all with : prefix)')

    # ln: link into mounted VFS
    ln = _CmdParser(prog='ln',
        description='Link local file/directory into mounted VFS.')
    ln.add_argument('paths', nargs='+', metavar='local_and_remote',
        help='local source(s) and device destination')

    # exec: execute code
    exec_cmd = _CmdParser(prog='exec',
        description='Execute Python code on device.')
    exec_cmd.add_argument('code', help='Python code to execute')

    # run: run local file
    run = _CmdParser(prog='run',
        description='Run local Python file on device.')
    run.add_argument('file', metavar='local_file', help='local .py file')
    run.add_argument('-m', '--monitor', action='store_true',
        help='monitor output after execution')

    # sleep: pause
    sleep = _CmdParser(prog='sleep',
        description='Pause for specified number of seconds.')
    sleep.add_argument('seconds', type=float, help='seconds to sleep')

    # ota: OTA update
    ota = _CmdParser(prog='ota',
        description='Perform OTA firmware update (ESP32).')
    ota.add_argument('firmware', help='firmware .app-bin file')

    # flash: flash operations (with subcommands)
    flash = _CmdParser(
        prog='flash',
        description='Flash/partition ops. Without args, show info.')
    flash_sub = flash.add_subparsers(dest='operation')
    # flash read [label] file
    flash_read = flash_sub.add_parser('read', help='read to file')
    flash_read.add_argument(
        'args', nargs='+', metavar='[label] file',
        help='destination file, optionally with partition label')
    # flash write [label] file
    flash_write = flash_sub.add_parser('write', help='write from file')
    flash_write.add_argument(
        'args', nargs='+', metavar='[label] file',
        help='source file, optionally with partition label')
    # flash erase [label] [--full]
    flash_erase = flash_sub.add_parser('erase', help='erase flash')
    flash_erase.add_argument(
        'label', nargs='?', help='partition label (ESP32)')
    flash_erase.add_argument(
        '--full', action='store_true', help='full erase (slow)')

    return {
        'cp': cp,
        'reset': reset,
        'mount': mount,
        'path': path,
        'ls': ls,
        'tree': tree,
        'cat': cat,
        'mkdir': mkdir,
        'rm': rm,
        'cd': cd,
        'mv': mv,
        'ln': ln,
        'exec': exec_cmd,
        'run': run,
        'sleep': sleep,
        'ota': ota,
        'flash': flash,
        'pwd': _CmdParser(prog='pwd',
            description='Print current working directory on device.'),
        'info': _CmdParser(
            prog='info',
            description='Show device info (platform, memory, filesystem).'),
        'stop': _CmdParser(prog='stop',
            description='Stop running program on device (send Ctrl-C).'),
        'repl': _CmdParser(prog='repl',
            description='Interactive REPL session. Press Ctrl-] to exit.'),
        'monitor': _CmdParser(prog='monitor',
            description='Monitor device output. Press Ctrl-C to stop.'),
        'speedtest': _CmdParser(prog='speedtest',
            description='Test serial link speed.'),
    }


_SUBPARSERS = _build_subparsers()


class MpyTool():
    SPACE = '   '
    BRANCH = '│  '
    TEE = '├─ '
    LAST = '└─ '

    def __init__(
            self, conn=None, log=None, verbose=None, exclude_dirs=None,
            force=False, compress=None, chunk_size=None,
            port=None, address=None, baudrate=115200):
        # Connection can be provided directly or created lazily from parameters
        self._conn = conn
        self._port = port
        self._address = address
        self._baudrate = baudrate
        self._log = log if log is not None else SimpleColorLogger()
        self._verbose_out = verbose  # None = no verbose output (API mode)
        self._exclude_dirs = {'.*', '*.pyc', '__pycache__'}
        if exclude_dirs:
            self._exclude_dirs.update(exclude_dirs)
        self._chunk_size = chunk_size
        self._mpy = None
        self._force = force
        self._compress = compress
        self._progress_total_files = 0
        self._progress_current_file = 0
        self._progress_src = ''
        self._progress_dst = ''
        self._progress_max_src_len = 0
        self._progress_max_dst_len = 0
        self._is_debug = getattr(self._log, '_loglevel', 1) >= 4
        self._batch_mode = False
        self._skipped_files = 0
        self._stats_total_bytes = 0
        self._stats_transferred_bytes = 0
        self._stats_wire_bytes = 0  # Bytes sent over wire (with encoding)
        self._stats_transferred_files = 0
        self._stats_start_time = None
        self._mpy_cross = None  # MpyCross instance when --mpy active
        # Remote file info cache for batch operations
        self._remote_file_cache = {}  # {path: (size, hash) or None}

    @property
    def conn(self):
        """Lazy connection initialization with auto-detection"""
        if self._conn is None:
            port = self._port
            if not port and not self._address:
                # Auto-detect serial port
                ports = _utils.detect_serial_ports()
                if not ports:
                    raise _mpytool.ConnError(
                        "No serial port found. Use -p to specify port.")
                if len(ports) > 1:
                    ports_str = ', '.join(ports)
                    raise _mpytool.ConnError(
                        f"Multiple serial ports found: {ports_str}. "
                        "Use -p to specify one.")
                port = ports[0]
                self.verbose(f"Using {port}", level=2)
            if port:
                self._conn = _mpytool.ConnSerial(
                    port=port, baudrate=self._baudrate, log=self._log)
            elif self._address:
                self._conn = _mpytool.ConnSocket(
                    address=self._address, log=self._log)
        return self._conn

    @property
    def mpy(self):
        """Lazy Mpy initialization"""
        if self._mpy is None:
            self._mpy = _mpytool.Mpy(
                self.conn, log=self._log, chunk_size=self._chunk_size)
        return self._mpy

    def _is_excluded(self, name):
        """Check if name matches any exclude pattern (supports wildcards)"""
        for pattern in self._exclude_dirs:
            if _fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _collect_flags(self, commands):
        """Collect flag arguments from commands list.

        Pops flags (starting with -) from commands until a non-flag is found.
        Handles -t VALUE style arguments.
        """
        flags = []
        while commands and commands[0].startswith('-'):
            flags.append(commands.pop(0))
            # Handle -t/--timeout VALUE style
            if flags[-1] in ('-t', '--timeout') and commands:
                if not commands[0].startswith('-'):
                    flags.append(commands.pop(0))
        return flags

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

    def print_transfer_info(self):
        """Print transfer settings (chunk size and compression)"""
        chunk = self.mpy._detect_chunk_size()
        chunk_str = f"{chunk // 1024}K" if chunk >= 1024 else str(chunk)
        if self._compress is None:
            compress = self.mpy._detect_deflate()
        else:
            compress = self._compress
        compress_str = "on" if compress else "off"
        if self._verbose >= 2:
            self.verbose(f"COPY (chunk: {chunk_str}, compress: {compress_str})")
        else:
            self.verbose("COPY")

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

    # Encoding info strings for alignment:
    # (base64), (compressed), (base64, compressed), (unchanged)
    _ENC_WIDTH = 22  # Length of longest: "  (base64, compressed)"

    def _format_encoding_info(self, encodings, pad=False):
        """Format encoding: (base64), (compressed), (base64, compressed)"""
        if not encodings or encodings == {'raw'}:
            return " " * self._ENC_WIDTH if pad else ""
        types = sorted(e for e in encodings if e != 'raw')
        if not types:
            return " " * self._ENC_WIDTH if pad else ""
        info = f"  ({', '.join(types)})"
        if pad:
            return f"{info:<{self._ENC_WIDTH}}"
        return info

    def _format_progress_prefix(self):
        """Format progress prefix: [2/5] or empty string for single file"""
        if self._progress_total_files <= 1:
            return ""
        width = len(str(self._progress_total_files))
        n = self._progress_current_file
        m = self._progress_total_files
        return f"[{n:>{width}}/{m}]"

    def _format_line(self, status, total, encodings=None):
        """Format verbose line: [2/5] 100% 24.1K src -> dst (base64)"""
        size_str = _utils.format_size(total)
        multi = self._progress_total_files > 1
        prefix = self._format_progress_prefix()
        src_w = max(len(self._progress_src), self._progress_max_src_len)
        dst_w = max(len(self._progress_dst), self._progress_max_dst_len)
        if encodings:
            enc = self._format_encoding_info(encodings, pad=multi)
        else:
            enc = " " * self._ENC_WIDTH if multi else ""
        src = self._progress_src
        dst = self._progress_dst
        return (
            f"{prefix:>7} {status} {size_str:>5} "
            f"{src:<{src_w}} -> {dst:<{dst_w}}{enc}")

    def _format_compact_progress(self, status, total):
        """Format compact progress line: [2/5]  45% 24.1K source"""
        size_str = _utils.format_size(total)
        prefix = self._format_progress_prefix()
        return f"{prefix:>7} {status} {size_str:>5} {self._progress_src}"

    def _format_compact_complete(self, total):
        """Format compact complete line: 24.1K source -> dest"""
        size_str = _utils.format_size(total)
        return f" {size_str:>5} {self._progress_src} -> {self._progress_dst}"

    def _format_progress_line(self, percent, total, encodings=None):
        return self._format_line(f"{percent:3d}%", total, encodings)

    def _format_skip_line(self, total):
        return self._format_line("skip", total, {'unchanged'})

    def _progress_callback(self, transferred, total):
        """Callback for file transfer progress"""
        percent = (transferred * 100 // total) if total > 0 else 100
        if self._verbose >= 2:
            line = self._format_progress_line(percent, total)
        else:
            line = self._format_compact_progress(f"{percent:3d}%", total)
        if self._is_debug:
            self.verbose(line, color='cyan')
        else:
            self.verbose(line, color='cyan', end='', overwrite=True)

    def _progress_complete(self, total, encodings=None):
        """Mark current file as complete"""
        if self._verbose >= 2:
            line = self._format_progress_line(100, total, encodings)
        else:
            line = self._format_compact_complete(total)
        self.verbose(line, color='cyan', overwrite=not self._is_debug)

    def _set_progress_info(self, src, dst, is_src_remote, is_dst_remote):
        """Set progress source and destination paths"""
        if is_src_remote:
            self._progress_src = ':' + src
        else:
            self._progress_src = self._format_local_path(src)
        if is_dst_remote:
            if dst.startswith('/'):
                self._progress_dst = ':' + dst
            else:
                self._progress_dst = ':/' + dst
        else:
            self._progress_dst = self._format_local_path(dst)
        if len(self._progress_dst) > self._progress_max_dst_len:
            self._progress_max_dst_len = len(self._progress_dst)

    def _collect_local_paths(self, path):
        """Collect all local file paths (formatted for display)"""
        if _os.path.isfile(path):
            return [self._format_local_path(path)]
        paths = []
        for root, dirs, files in _os.walk(path, topdown=True):
            dirs[:] = [d for d in dirs if not self._is_excluded(d)]
            files = [f for f in files if not self._is_excluded(f)]
            for f in files:
                paths.append(self._format_local_path(_os.path.join(root, f)))
        return paths

    def _prefetch_remote_info(self, dst_files):
        """Prefetch remote file info (size and hash) for multiple files

        Arguments:
            dst_files: dict {remote_path: local_size}

        Results are cached in _remote_file_cache.
        """
        if self._force or not dst_files:
            return
        files_to_fetch = {
            p: s for p, s in dst_files.items()
            if p not in self._remote_file_cache}
        if not files_to_fetch:
            return
        self.verbose(f"Checking {len(files_to_fetch)} files...", 2)
        result = self.mpy.fileinfo(files_to_fetch)
        if result is None:
            # hashlib not available - mark all as needing update
            for path in files_to_fetch:
                self._remote_file_cache[path] = None
        else:
            self._remote_file_cache.update(result)

    def _collect_dst_files(self, src_path, dst_path, add_src_basename=True):
        """Collect destination paths and sizes for local->remote copy

        Arguments:
            src_path: local source path (file or directory)
            dst_path: remote destination base path
            add_src_basename: add source basename to dst_path

        Returns:
            dict {remote_path: local_file_size}
        """
        files = {}
        if _os.path.isfile(src_path):
            # For files, add basename if dst_path ends with /
            basename = _os.path.basename(src_path)
            if basename and not _os.path.basename(dst_path):
                dst_path = _join_remote_path(dst_path, basename)
            cache = self._mpy_cross and self._mpy_cross.compiled.get(src_path)
            if cache:
                dst_path = dst_path[:-3] + '.mpy'
                files[dst_path] = _os.path.getsize(cache)
            else:
                files[dst_path] = _os.path.getsize(src_path)
        elif _os.path.isdir(src_path):
            # For directories, mimic _put_dir behavior
            if add_src_basename:
                basename = _os.path.basename(_os.path.abspath(src_path))
                if basename:
                    dst_path = _join_remote_path(dst_path, basename)
            for root, dirs, filenames in _os.walk(src_path, topdown=True):
                dirs[:] = sorted(
                    d for d in dirs if not self._is_excluded(d))
                filenames = sorted(
                    f for f in filenames if not self._is_excluded(f))
                rp = _os.path.relpath(root, src_path)
                rel_path = rp.replace(_os.sep, '/')
                if rel_path == '.':
                    rel_path = ''
                for file_name in filenames:
                    spath = _os.path.join(root, file_name)
                    mpyc = self._mpy_cross
                    cache = mpyc and mpyc.compiled.get(spath)
                    if cache:
                        dst_name = file_name[:-3] + '.mpy'
                        dpath = _join_remote_path(
                            _join_remote_path(dst_path, rel_path), dst_name)
                        files[dpath] = _os.path.getsize(cache)
                    else:
                        dpath = _join_remote_path(
                            _join_remote_path(dst_path, rel_path), file_name)
                        files[dpath] = _os.path.getsize(spath)
        return files

    def _file_needs_update(self, local_data, remote_path):
        """Check if local file differs from remote file

        Returns True if file needs to be uploaded (different or doesn't exist)
        """
        if self._force:
            return True
        # Check cache first (populated by _prefetch_remote_info)
        if remote_path in self._remote_file_cache:
            cached = self._remote_file_cache[remote_path]
            if cached is None:
                return True  # File doesn't exist or hashlib not available
            remote_size, remote_hash = cached
            local_size = len(local_data)
            if local_size != remote_size:
                return True  # Different size
            if remote_hash is None:
                # Size matched but hash wasn't computed
                return True
            local_hash = _hashlib.sha256(local_data).digest()
            return local_hash != remote_hash
        # Fallback to individual calls (for single file operations)
        remote_size = self.mpy.stat(remote_path)
        if remote_size is None or remote_size < 0:
            return True  # File doesn't exist or is a directory
        local_size = len(local_data)
        if local_size != remote_size:
            return True  # Different size
        # Sizes match - check hash
        local_hash = _hashlib.sha256(local_data).digest()
        remote_hash = self.mpy.hashfile(remote_path)
        if remote_hash is None:
            return True  # hashlib not available on device
        return local_hash != remote_hash

    def _collect_source_paths(self, commands):
        """Collect source paths for cp/put (for alignment calculation)"""
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
                    paths.extend(self._collect_remote_paths(src_path))
                else:
                    paths.extend(self._collect_local_paths(src_path))
        return paths

    def _collect_remote_paths(self, path):
        """Collect all remote file paths (formatted for display)"""
        paths = []
        stat = self.mpy.stat(path)
        if stat is None:
            return paths
        if stat >= 0:  # file
            paths.append(':' + path)
        else:  # directory
            entries = self.mpy.ls(path)
            for name, size in entries:
                entry_path = path.rstrip('/') + '/' + name
                if size is None:  # directory
                    paths.extend(self._collect_remote_paths(entry_path))
                else:  # file
                    paths.append(':' + entry_path)
        return paths

    def _collect_destination_paths(self, commands):
        """Collect formatted destination paths for a cp/put command"""
        if not commands:
            return []
        cmd = commands[0]
        if cmd == 'cp' and len(commands) >= 3:
            # Filter out flags
            args = [a for a in commands[1:] if not a.startswith('-')]
            if len(args) < 2:
                return []
            sources, dest = args[:-1], args[-1]
            dest_is_remote = dest.startswith(':')
            dest_path = (dest[1:] or '/') if dest_is_remote else dest
            dest_is_dir = dest_path.endswith('/')
            dst_paths = []
            for src in sources:
                src_is_remote = src.startswith(':')
                if src_is_remote:
                    src_path = (src[1:] or '/').rstrip('/') or '/'
                else:
                    src_path = src.rstrip('/') or '/'
                copy_contents = src.endswith('/')
                if dest_is_remote and not src_is_remote:
                    # local -> remote: reuse _collect_dst_files
                    if _os.path.exists(src_path):
                        base = dest_path.rstrip('/') or '/'
                        files = self._collect_dst_files(
                            src_path, base, not copy_contents)
                        dst_paths.extend(':' + p for p in files)
                elif not dest_is_remote and src_is_remote:
                    # remote -> local
                    dst_paths.extend(self._collect_remote_to_local_dst(
                        src_path, dest_path, dest_is_dir, copy_contents))
                elif dest_is_remote and src_is_remote:
                    # remote -> remote (file only)
                    stat = self.mpy.stat(src_path)
                    if stat is not None and stat >= 0:
                        if dest_is_dir:
                            basename = _remote_basename(src_path)
                            dst = _join_remote_path(dest_path, basename)
                        else:
                            dst = dest_path
                        dst_paths.append(':' + dst)
            return dst_paths
        return []

    def _collect_remote_to_local_dst(
            self, src_path, dest_path, dest_is_dir, copy_contents):
        """Collect destination paths for remote->local copy"""
        stat = self.mpy.stat(src_path)
        if stat is None:
            return []
        base_dst = dest_path.rstrip('/') or '.'
        if dest_is_dir and not copy_contents and src_path != '/':
            base_dst = _os.path.join(base_dst, _remote_basename(src_path))
        if stat >= 0:  # file
            if _os.path.isdir(base_dst) or dest_is_dir:
                basename = _remote_basename(src_path)
                return [self._format_local_path(
                    _os.path.join(base_dst, basename))]
            return [self._format_local_path(base_dst)]
        return self._collect_remote_dir_dst(src_path, base_dst)

    def _collect_remote_dir_dst(self, src_path, base_dst):
        """Collect local destination paths for remote directory download"""
        paths = []
        for name, size in self.mpy.ls(src_path):
            entry_src = src_path.rstrip('/') + '/' + name
            entry_dst = _os.path.join(base_dst, name)
            if size is None:  # directory
                paths.extend(self._collect_remote_dir_dst(entry_src, entry_dst))
            else:  # file
                paths.append(self._format_local_path(entry_dst))
        return paths

    def count_files_for_command(self, commands):
        """Count files for cp/put command.
        Returns (is_copy_command, file_count, source_paths, dest_paths)"""
        src_paths = self._collect_source_paths(commands)
        if src_paths:
            dst_paths = self._collect_destination_paths(commands)
            return True, len(src_paths), src_paths, dst_paths
        return False, 0, [], []

    def set_batch_progress(self, total_files, max_src_len=0, max_dst_len=0):
        """Set batch progress for consecutive copy commands"""
        self._progress_total_files = total_files
        self._progress_current_file = 0
        self._progress_max_src_len = max_src_len
        self._progress_max_dst_len = max_dst_len
        self._batch_mode = True
        self._stats_total_bytes = 0
        self._stats_transferred_bytes = 0
        self._stats_wire_bytes = 0
        self._stats_transferred_files = 0
        self._skipped_files = 0
        self._stats_start_time = _time.time()

    def reset_batch_progress(self):
        """Reset batch progress mode"""
        self._batch_mode = False
        self._progress_total_files = 0
        self._progress_current_file = 0
        self._progress_max_src_len = 0
        self._progress_max_dst_len = 0
        self._remote_file_cache.clear()

    def print_copy_summary(self):
        """Print summary after copy operation"""
        if self._stats_start_time is None:
            return
        elapsed = _time.time() - self._stats_start_time
        total = self._stats_total_bytes
        transferred = self._stats_transferred_bytes
        wire = self._stats_wire_bytes
        total_files = self._stats_transferred_files + self._skipped_files
        parts = []
        parts.append(f"{_utils.format_size(transferred).strip()}")
        if elapsed > 0:
            speed = transferred / elapsed
            parts.append(f"{_utils.format_size(speed).strip()}/s")
        parts.append(f"{elapsed:.1f}s")
        # Combined speedup: total file size vs actual wire bytes
        # Includes savings from: skipped files, base64 encoding, compression
        if wire > 0 and total > wire:
            speedup = total / wire
            parts.append(f"speedup {speedup:.1f}x")
        summary = "  ".join(parts)
        if self._skipped_files > 0:
            t = self._stats_transferred_files
            s = self._skipped_files
            file_info = f"{t} transferred, {s} unchanged"
        else:
            file_info = f"{total_files} files"
        self.verbose(f" {summary}  ({file_info})", color='green')

    @classmethod
    def print_tree(cls, tree, prefix='', print_size=True, first=True,
            last=True):
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
        if sub_tree is not None and name != '/':
            sufix = '/'
        # For root, display './' only for empty path (CWD)
        display_name = '.' if first and name in ('', '.') else name
        line = ''
        if print_size:
            line += f'{_utils.format_size(size):>9} '
        line += prefix + this_prefix + display_name + sufix
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

    def _upload_file(self, data, src_path, dst_path, show_progress):
        """Upload file data to device with stats and progress"""
        # Use compiled .mpy data if available
        cache = self._mpy_cross and self._mpy_cross.compiled.get(src_path)
        if cache:
            with open(cache, 'rb') as f:
                data = f.read()
            if dst_path.endswith('.py'):
                dst_path = dst_path[:-3] + '.mpy'
        file_size = len(data)
        self._stats_total_bytes += file_size
        if show_progress and self._verbose >= 1:
            self._progress_current_file += 1
            self._set_progress_info(src_path, dst_path, False, True)
            # Show CHCK status during checksum verification
            if self._verbose >= 2:
                line = self._format_line("CHCK", file_size)
            else:
                line = self._format_compact_progress("CHCK", file_size)
            self.verbose(line, color='cyan', end='', overwrite=True)
        if not self._file_needs_update(data, dst_path):
            self._skipped_files += 1
            if show_progress and self._verbose >= 2:
                self.verbose(
                    self._format_skip_line(file_size),
                    color='yellow', overwrite=True)
            elif show_progress and self._verbose >= 1:
                # Erase CHCK line (next file's progress will overwrite)
                self.verbose("", end='', overwrite=True)
            return False  # skipped
        self._stats_transferred_bytes += file_size
        self._stats_transferred_files += 1
        if show_progress and self._verbose >= 1:
            encodings, wire = self.mpy.put(
                data, dst_path, self._progress_callback, self._compress)
            self._stats_wire_bytes += wire
            self._progress_complete(file_size, encodings)
        else:
            _, wire = self.mpy.put(data, dst_path, compress=self._compress)
            self._stats_wire_bytes += wire
        return True  # uploaded

    def _put_dir(self, src_path, dst_path, show_progress=True,
            target_name=None):
        """Upload directory to device

        Arguments:
            src_path: local source directory
            dst_path: remote destination parent directory
            show_progress: show progress bar
            target_name: use as directory name instead of src basename
        """
        if target_name is not None:
            basename = target_name
        else:
            basename = _os.path.basename(_os.path.abspath(src_path))
        if basename:
            dst_path = _join_remote_path(dst_path, basename)
        self.verbose(f"PUT DIR: {src_path} -> {dst_path}", 2)
        created_dirs = set()
        for path, dirs, files in _os.walk(src_path, topdown=True):
            dirs[:] = sorted(d for d in dirs if not self._is_excluded(d))
            files = sorted(f for f in files if not self._is_excluded(f))
            if not files:
                continue
            rp = _os.path.relpath(path, src_path).replace(_os.sep, '/')
            rel_path = '' if rp == '.' else rp
            remote_dir = _join_remote_path(dst_path, rel_path)
            if remote_dir and remote_dir not in created_dirs:
                self.verbose(f'MKDIR: {remote_dir}', 2)
                self.mpy.mkdir(remote_dir)
                created_dirs.add(remote_dir)
            for file_name in files:
                spath = _os.path.join(path, file_name)
                with open(spath, 'rb') as f:
                    dst = _join_remote_path(remote_dir, file_name)
                    self._upload_file(f.read(), spath, dst, show_progress)

    def _put_file(self, src_path, dst_path, show_progress=True):
        basename = _os.path.basename(src_path)
        if basename and not _os.path.basename(dst_path):
            dst_path = _join_remote_path(dst_path, basename)
        self.verbose(f"PUT FILE: {src_path} -> {dst_path}", 2)
        with open(src_path, 'rb') as f:
            data = f.read()
        parent = _os.path.dirname(dst_path)
        if parent:
            stat = self.mpy.stat(parent)
            if stat is None:
                self.mpy.mkdir(parent)
            elif stat >= 0:
                raise _mpytool.MpyError(
                    f'Error creating file under file: {parent}')
        self._upload_file(data, src_path, dst_path, show_progress)

    def _get_file(self, src_path, dst_path, show_progress=True):
        """Download single file from device"""
        self.verbose(f"GET FILE: {src_path} -> {dst_path}", 2)
        dst_dir = _os.path.dirname(dst_path)
        if dst_dir and not _os.path.exists(dst_dir):
            _os.makedirs(dst_dir)
        if show_progress and self._verbose >= 1:
            self._progress_current_file += 1
            self._set_progress_info(src_path, dst_path, True, False)
            data = self.mpy.get(src_path, self._progress_callback)
            self._progress_complete(len(data), None)
        else:
            data = self.mpy.get(src_path)
        file_size = len(data)
        self._stats_total_bytes += file_size
        self._stats_transferred_bytes += file_size
        self._stats_transferred_files += 1
        with open(dst_path, 'wb') as dst_file:
            dst_file.write(data)

    def _get_dir(self, src_path, dst_path, copy_contents=False,
            show_progress=True):
        """Download directory from device"""
        if not copy_contents:
            basename = _remote_basename(src_path)
            if basename:
                dst_path = _os.path.join(dst_path, basename)
        self.verbose(f"GET DIR: {src_path} -> {dst_path}", 2)
        if not _os.path.exists(dst_path):
            _os.makedirs(dst_path)
        entries = self.mpy.ls(src_path)
        for name, size in entries:
            src_entry = src_path.rstrip('/') + '/' + name
            dst_entry = _os.path.join(dst_path, name)
            if size is None:  # directory
                self._get_dir(
                    src_entry, dst_entry,
                    copy_contents=True, show_progress=show_progress)
            else:  # file
                self._get_file(
                    src_entry, dst_entry, show_progress=show_progress)

    def _cp_local_to_remote(self, src_path, dst_path, dst_is_dir):
        """Upload local file/dir to device

        Path semantics:
        - dst_is_dir=True: add src basename to dst (unless copy_contents)
        - dst_is_dir=False: dst is target name (rename)
        - copy_contents (src ends with /): copy contents, not directory
        """
        src_is_dir = _os.path.isdir(src_path.rstrip('/'))
        copy_contents = src_path.endswith('/')
        src_path = src_path.rstrip('/')
        if not _os.path.exists(src_path):
            raise ParamsError(f'Source not found: {src_path}')
        # Normalize dst_path (remove trailing slash, but keep '/' for root)
        if dst_path == '/':
            pass  # Keep '/' as is
        elif dst_path:
            dst_path = dst_path.rstrip('/')
        else:
            dst_path = ''
        if src_is_dir:
            if copy_contents:
                # Copy directory contents to dst_path
                for item in _os.listdir(src_path):
                    if self._is_excluded(item):
                        continue
                    item_src = _os.path.join(src_path, item)
                    if _os.path.isdir(item_src):
                        self._put_dir(item_src, dst_path)
                    else:
                        dst = _join_remote_path(dst_path, item)
                        self._put_file(item_src, dst)
            elif dst_is_dir:
                # Copy dir to dest directory (_put_dir adds basename)
                self._put_dir(src_path, dst_path)
            else:
                # Rename: copy directory with new name
                parent = _os.path.dirname(dst_path)
                target_name = _os.path.basename(dst_path)
                self._put_dir(src_path, parent, target_name=target_name)
        else:
            # File: add basename if dst_is_dir
            if dst_is_dir:
                basename = _os.path.basename(src_path)
                dst_path = _join_remote_path(dst_path, basename)
            self._put_file(src_path, dst_path)

    def _cp_remote_to_local(self, src_path, dst_path, dst_is_dir):
        """Download file/dir from device to local"""
        copy_contents = src_path.endswith('/')
        src_path = src_path.rstrip('/') or '/'
        stat = self.mpy.stat(src_path)
        if stat is None:
            raise ParamsError(f'Source not found on device: {src_path}')
        src_is_dir = (stat == -1)
        if dst_is_dir:
            if not _os.path.exists(dst_path):
                _os.makedirs(dst_path)
            if not copy_contents and src_path != '/':
                dst_path = _os.path.join(dst_path, _remote_basename(src_path))
        if src_is_dir:
            self._get_dir(src_path, dst_path, copy_contents=copy_contents)
        else:
            if _os.path.isdir(dst_path):
                dst_path = _os.path.join(dst_path, _remote_basename(src_path))
            self._get_file(src_path, dst_path)

    def _cp_remote_to_remote(self, src_path, dst_path, dst_is_dir):
        """Copy file on device"""
        src_path = src_path.rstrip('/') or '/'
        stat = self.mpy.stat(src_path)
        if stat is None:
            raise ParamsError(f'Source not found on device: {src_path}')
        if stat == -1:
            raise ParamsError(
                'Remote-to-remote directory copy not supported')
        if dst_is_dir:
            dst_path = _join_remote_path(dst_path, _remote_basename(src_path))
        self.verbose(f"COPY: {src_path} -> {dst_path}", 2)
        if self._verbose >= 1:
            self._progress_current_file += 1
            self._set_progress_info(src_path, dst_path, True, True)
            data = self.mpy.get(src_path, self._progress_callback)
            encodings, wire = self.mpy.put(
                data, dst_path, compress=self._compress)
            self._stats_wire_bytes += wire
            self._progress_complete(len(data), encodings)
        else:
            data = self.mpy.get(src_path)
            _, wire = self.mpy.put(data, dst_path, compress=self._compress)
            self._stats_wire_bytes += wire
        file_size = len(data)
        self._stats_total_bytes += file_size
        self._stats_transferred_bytes += file_size
        self._stats_transferred_files += 1

    def cmd_cp(self, *cmd_args):
        """Copy files between local and device"""
        # Support both cmd_cp(['a', 'b']) and cmd_cp('a', 'b')
        if len(cmd_args) == 1 and isinstance(cmd_args[0], list):
            cmd_args = cmd_args[0]
        args = _SUBPARSERS['cp'].parse_args(cmd_args)
        if len(args.paths) < 2:
            raise ParamsError('cp requires source and destination')
        # Determine compress setting
        if args.no_compress:
            compress = False
        elif args.compress:
            compress = True
        else:
            compress = None  # Use global setting
        # Save and set flags for this command
        saved_force = self._force
        saved_compress = self._compress
        saved_mpy_cross = self._mpy_cross
        if args.force:
            self._force = True
        if args.mpy:
            self._mpy_cross = MpyCross(self._log, self.verbose)
        if compress is not None:
            self._compress = compress
        try:
            self._cmd_cp_impl(args.paths)
        finally:
            self._force = saved_force
            self._compress = saved_compress
            self._mpy_cross = saved_mpy_cross

    def _cmd_cp_impl(self, args):
        sources = list(args[:-1])
        dest = args[-1]
        dest_is_remote = dest.startswith(':')
        dest_path = dest[1:] if dest_is_remote else dest
        # Initialize mpy-cross compilation if requested
        if self._mpy_cross:
            self._mpy_cross.init(self.mpy.platform())
            if self._mpy_cross.active:
                self._mpy_cross.compile_sources(
                    sources, self._is_excluded)
        # Determine if destination is a directory:
        # - Remote: '' (CWD) or '/' (root) or ends with '/'
        # - Local: ends with '/' or exists as directory
        if dest_is_remote:
            dest_is_dir = (
                dest_path == '' or dest_path == '/'
                or dest_path.endswith('/'))
        else:
            dest_is_dir = (
                dest_path.endswith('/') or _os.path.isdir(dest_path))
        # Check: contents (trailing slash) or multiple sources
        has_multi_source = len(sources) > 1
        # any source has trailing /
        has_contents_copy = any(s.rstrip('/') != s for s in sources)
        if (has_multi_source or has_contents_copy) and not dest_is_dir:
            raise ParamsError(
                'multiple sources or directory contents require '
                'destination directory (ending with /)')
        if self._verbose >= 1 and not self._batch_mode:
            total_files = 0
            for src in sources:
                src_is_remote = src.startswith(':')
                src_path = src[1:] if src_is_remote else src
                if not src_path:
                    src_path = '/'
                src_path_clean = src_path.rstrip('/') or '/'
                if src_is_remote:
                    paths = self._collect_remote_paths(src_path_clean)
                else:
                    paths = self._collect_local_paths(src_path_clean)
                total_files += len(paths)
            self._progress_total_files = total_files
            self._progress_current_file = 0
        all_dst_files = {}
        if dest_is_remote:
            for src in sources:
                if not src.startswith(':'):  # local source
                    copy_contents = src.endswith('/')
                    src_path = src.rstrip('/')
                    if _os.path.exists(src_path):
                        add_basename = dest_is_dir and not copy_contents
                        base_path = dest_path.rstrip('/') if dest_path else ''
                        all_dst_files.update(self._collect_dst_files(
                            src_path, base_path, add_basename))
            if all_dst_files and not self._batch_mode:
                self._progress_max_dst_len = max(
                    len(':' + p) for p in all_dst_files)
                if not self._force:
                    self._prefetch_remote_info(all_dst_files)
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
        dest_path = _parse_device_path(dest, 'mv destination')
        for src in sources:
            _parse_device_path(src, 'mv source')  # validate only
        # ':' = CWD (empty string), ':/' = root
        dest_is_dir = (
            dest_path == '' or dest_path == '/'
            or dest_path.endswith('/'))
        if len(sources) > 1 and not dest_is_dir:
            raise ParamsError(
                'multiple sources require destination directory '
                '(ending with /)')
        self.mpy.import_module('os')
        for src in sources:
            src_path = _parse_device_path(src, 'mv')
            stat = self.mpy.stat(src_path)
            if stat is None:
                raise ParamsError(f'Source not found on device: {src_path}')
            if dest_is_dir:
                # Preserve '/' as root, strip trailing slash from others
                if dest_path == '/':
                    dst_dir = '/'
                else:
                    dst_dir = dest_path.rstrip('/')
                if dst_dir and dst_dir != '/':
                    if self.mpy.stat(dst_dir) is None:
                        self.mpy.mkdir(dst_dir)
                basename = _remote_basename(src_path)
                final_dest = _join_remote_path(dst_dir, basename)
            else:
                final_dest = dest_path
            self.verbose(f"MV: {src_path} -> {final_dest}", 1)
            self.mpy.rename(src_path, final_dest)

    def cmd_rm(self, *file_names):
        """Delete files/directories on device"""
        for file_name in file_names:
            raw_path = _parse_device_path(file_name, 'rm')
            contents_only = raw_path.endswith('/') or raw_path == ''
            # ':' = CWD, ':/' = root, ':/path' = path, ':path/' = contents
            if raw_path == '':
                path = ''  # CWD
            elif raw_path == '/':
                path = '/'  # root
            else:
                path = raw_path.rstrip('/') if contents_only else raw_path
            if contents_only:
                self.verbose(f"RM contents: {path or 'CWD'}", 1)
                entries = self.mpy.ls(path)
                for name, size in entries:
                    entry_path = _join_remote_path(path, name)
                    self.verbose(f"  {entry_path}", 1)
                    self.mpy.delete(entry_path)
            else:
                self.verbose(f"RM: {path}", 1)
                self.mpy.delete(path)

    def cmd_monitor(self):
        self.verbose("MONITOR (Ctrl+C to stop)", 1)
        try:
            while True:
                line = self.conn.read_line()
                line = line.decode('utf-8', 'backslashreplace')
                print(line)
        except KeyboardInterrupt:
            self.verbose('', level=0, overwrite=True)  # newline after ^C
        except (_mpytool.ConnError, OSError) as err:
            if self._log:
                self._log.error(err)

    def cmd_repl(self):
        self.mpy.comm.exit_raw_repl()
        log = self._verbose_out
        if not _terminal.AVAILABLE:
            self._log.error("REPL not available on this platform")
            return
        msg = f"REPL (Ctrl+] to exit)"
        self.verbose(msg, 1)
        terminal = _terminal.Terminal(self.conn, self._log)
        terminal.run()
        self._log.info('Exiting..')

    def cmd_ota(self, firmware_path):
        """OTA firmware update from local .app-bin file"""
        self.verbose("OTA UPDATE", 1)
        if not _os.path.isfile(firmware_path):
            raise ParamsError(f"Firmware file not found: {firmware_path}")

        with open(firmware_path, 'rb') as f:
            firmware = f.read()

        fw_size = len(firmware)
        self.verbose(f"  Firmware: {_utils.format_size(fw_size)}", 1)
        info = self.mpy.partitions()
        if not info['next_ota']:
            raise _mpytool.MpyError("OTA not available (no OTA partitions)")

        ota_size = _utils.format_size(info['next_ota_size'])
        self.verbose(f"  Target: {info['next_ota']} ({ota_size})", 1)
        use_compress = self.mpy._detect_deflate()
        chunk_size = self.mpy._detect_chunk_size()
        if chunk_size >= 1024:
            chunk_str = f"{chunk_size // 1024}K"
        else:
            chunk_str = str(chunk_size)
        comp_str = 'on' if use_compress else 'off'
        self.verbose(
            f"  Writing (chunk: {chunk_str}, compress: {comp_str})...", 1)
        start_time = _time.time()

        def progress_callback(transferred, total, wire_bytes):
            if self._verbose >= 1:
                pct = transferred * 100 // total
                elapsed = _time.time() - start_time
                speed = transferred / elapsed / 1024 if elapsed > 0 else 0
                t_str = _utils.format_size(transferred)
                tot_str = _utils.format_size(total)
                line = (
                    f"  Writing: {pct:3d}% {t_str:>6} / {tot_str}"
                    f"  {speed:.1f} KB/s")
                self.verbose(line, color='cyan', end='', overwrite=True)

        result = self.mpy.ota_write(
            firmware, progress_callback, self._compress)
        elapsed = _time.time() - start_time
        speed = fw_size / elapsed / 1024 if elapsed > 0 else 0
        wire = result['wire_bytes']
        ratio = fw_size / wire if wire > 0 else 1
        sz = _utils.format_size(fw_size)
        self.verbose(
            f"  Writing: 100% {sz:>6}  {elapsed:.1f}s  "
            f"{speed:.1f} KB/s  ratio {ratio:.2f}x",
            color='cyan', overwrite=True)

        self.verbose(
            "  OTA complete! Use 'mreset' to boot into new firmware.",
            1, color='green')

    def cmd_flash(self):
        """Show flash information (auto-detect platform)"""
        self.verbose("FLASH", 2)
        platform = self.mpy.platform()['platform']

        if platform == 'rp2':
            self._cmd_flash_rp2()
        elif platform == 'esp32':
            self._cmd_flash_esp32()
        else:
            raise _mpytool.MpyError(
                f"Flash info not supported for platform: {platform}")

    def _cmd_flash_rp2(self):
        """Show RP2 flash information"""
        info = self.mpy.flash_info()
        print(f"Platform:    RP2")
        print(f"Flash size:  {_utils.format_size(info['size'])}")
        print(f"Block size:  {info['block_size']} bytes")
        print(f"Block count: {info['block_count']}")
        fs_line = f"Filesystem:  {info['filesystem']}"
        # For FAT, show cluster size if detected from magic
        if info.get('fs_block_size'):
            fs_block = _utils.format_size(info['fs_block_size'])
            fs_line += f" (cluster: {fs_block})"
        if info['filesystem'] == 'unknown' and info.get('magic'):
            magic_hex = ' '.join(f'{b:02x}' for b in info['magic'])
            fs_line += f"  (magic: {magic_hex})"
        print(fs_line)

    def _make_progress(self, action, label=None):
        """Create progress callback for flash operations"""
        def progress(transferred, total, *_):
            if self._verbose >= 1:
                pct = (transferred / total * 100) if total > 0 else 0
                prefix = f"{action} {label}" if label else action
                t = _utils.format_size(transferred)
                tot = _utils.format_size(total)
                self.verbose(
                    f"  {prefix}: {pct:.0f}% {t} / {tot}",
                    color='cyan', end='', overwrite=True)
        return progress

    def cmd_flash_read(self, dest_path, label=None):
        """Read flash/partition content to file"""
        if label:
            self.verbose(f"FLASH READ {label} -> {dest_path}", 1)
        else:
            self.verbose(f"FLASH READ -> {dest_path}", 1)

        cb = self._make_progress("reading", label)
        data = self.mpy.flash_read(label=label, progress_callback=cb)

        if self._verbose >= 1:
            print()  # newline after progress

        with open(dest_path, 'wb') as f:
            f.write(data)

        sz = _utils.format_size(len(data))
        self.verbose(f"  saved {sz} to {dest_path}", 1, color='green')

    def cmd_flash_write(self, src_path, label=None):
        """Write file content to flash/partition"""
        if label:
            self.verbose(f"FLASH WRITE {src_path} -> {label}", 1)
        else:
            self.verbose(f"FLASH WRITE {src_path}", 1)

        with open(src_path, 'rb') as f:
            data = f.read()

        result = self.mpy.flash_write(
            data, label=label,
            progress_callback=self._make_progress("writing", label),
            compress=self._compress)

        if self._verbose >= 1:
            print()  # newline after progress

        target = label or "flash"
        comp_info = " (compressed)" if result.get('compressed') else ""
        sz = _utils.format_size(result['written'])
        self.verbose(
            f"  wrote {sz} to {target}{comp_info}", 1, color='green')

    def cmd_flash_erase(self, label=None, full=False):
        """Erase flash/partition (filesystem reset)"""
        mode = "full" if full else "quick"
        if label:
            self.verbose(f"FLASH ERASE {label} ({mode})", 1)
        else:
            self.verbose(f"FLASH ERASE ({mode})", 1)

        result = self.mpy.flash_erase(
            label=label, full=full,
            progress_callback=self._make_progress("erasing", label))

        if self._verbose >= 1:
            print()  # newline after progress

        target = label or "flash"
        sz = _utils.format_size(result['erased'])
        self.verbose(f"  erased {sz} from {target}", 1, color='green')
        if not label:
            self.verbose(
                "  filesystem will be recreated on next boot",
                1, color='yellow')

    def _cmd_flash_esp32(self):
        """List ESP32 partitions"""
        info = self.mpy.partitions()
        print(
            f"{'Label':<12} {'Type':<8} {'Subtype':<10} "
            f"{'Address':>10} {'Size':>10} "
            f"{'Block':>8} {'Actual FS':<12} {'Flags'}")
        print("-" * 90)

        for p in info['partitions']:
            flags = []
            if p['encrypted']:
                flags.append('enc')
            if p['running']:
                flags.append('running')
            # Block size column
            block_str = ''
            if p.get('fs_block_size'):
                block_str = _utils.format_size(p['fs_block_size'])
            # Filesystem column
            fs_info = ''
            if p.get('filesystem'):
                fs_info = p['filesystem']
                # For FAT, append cluster size
                if p.get('fs_cluster_size'):
                    cls = _utils.format_size(p['fs_cluster_size'])
                    fs_info += f" ({cls})"
            sz = _utils.format_size(p['size'])
            print(
                f"{p['label']:<12} {p['type_name']:<8} "
                f"{p['subtype_name']:<10} "
                f"{p['offset']:>#10x} {sz:>10} "
                f"{block_str:>8} {fs_info:<12} {', '.join(flags)}")

        if info['boot']:
            print(f"\nBoot partition: {info['boot']}")
        if info['next_ota']:
            print(f"Next OTA:       {info['next_ota']}")

    def cmd_info(self):
        self.verbose("INFO", 2)
        plat = self.mpy.platform()
        print(f"Platform:    {plat['platform']}")
        print(f"Version:     {plat['version']}")
        ver = list(plat['impl_version'])
        while ver and ver[-1] == 0:
            ver.pop()
        impl_ver = '.'.join(str(v) for v in ver)
        if plat['mpy_ver'] is not None:
            mpy_info = f", mpy v{plat['mpy_ver']}.{plat['mpy_sub']}"
        else:
            mpy_info = ""
        print(f"Impl:        {plat['impl_name']} {impl_ver}{mpy_info}")
        if plat['machine']:
            print(f"Machine:     {plat['machine']}")
        uid = self.mpy.unique_id()
        if uid:
            print(f"Serial:      {uid}")
        for iface, mac in self.mpy.mac_addresses():
            print(f"MAC {iface + ':':<8} {mac}")
        mem = self.mpy.memory()
        if mem['total'] > 0:
            mem_pct = mem['alloc'] / mem['total'] * 100
        else:
            mem_pct = 0
        alloc = _utils.format_size(mem['alloc'])
        total = _utils.format_size(mem['total'])
        print(f"Memory:      {alloc} / {total} ({mem_pct:.2f}%)")
        for fs in self.mpy.filesystems():
            if fs['mount'] == '/':
                label = "Flash:"
            else:
                label = fs['mount'] + ':'
            if fs['total'] > 0:
                fs_pct = fs['used'] / fs['total'] * 100
            else:
                fs_pct = 0
            used = _utils.format_size(fs['used'])
            total = _utils.format_size(fs['total'])
            print(f"{label:12} {used} / {total} ({fs_pct:.2f}%)")

    def cmd_reset(self, mode='soft', reconnect=True, timeout=None):
        """Reset device in specified mode

        Modes:
            soft     - Ctrl-D soft reset, runs boot.py/main.py (default)
            raw      - soft reset in raw REPL, clears RAM only
            machine  - machine.reset() with optional reconnect
            rts      - hardware reset via DTR/RTS with optional reconnect
            boot     - enter bootloader via machine.bootloader()
            dtr-boot - enter bootloader via DTR/RTS signals (ESP32)
        """
        self.verbose(f"RESET {mode}", 1)
        if mode == 'soft':
            self.mpy.soft_reset()
        elif mode == 'raw':
            self.mpy.soft_reset_raw()
        elif mode == 'machine':
            if reconnect:
                try:
                    self.verbose("  reconnecting...", 1, color='yellow')
                    self.mpy.machine_reset(
                        reconnect=True, timeout=timeout)
                    self.verbose("  connected", 1, color='green')
                except (_mpytool.ConnError, OSError) as err:
                    self.verbose(
                        f"  reconnect failed: {err}", 1, color='red')
                    raise _mpytool.ConnError(
                        f"Reconnect failed: {err}")
            else:
                self.mpy.machine_reset(reconnect=False)
        elif mode == 'rts':
            try:
                self.mpy.hard_reset()
                if reconnect:
                    self.verbose(
                        "  reconnecting...", 1, color='yellow')
                    _time.sleep(1.0)  # Wait for device to boot
                    self.mpy._conn.reconnect()
                    self.verbose("  connected", 1, color='green')
            except NotImplementedError:
                raise _mpytool.MpyError(
                    "Hardware reset not available (serial only)")
            except (_mpytool.ConnError, OSError) as err:
                self.verbose(
                    f"  reconnect failed: {err}", 1, color='red')
                raise _mpytool.ConnError(
                    f"Reconnect failed: {err}")
        elif mode == 'boot':
            self.mpy.machine_bootloader()
        elif mode == 'dtr-boot':
            try:
                self.mpy.reset_to_bootloader()
            except NotImplementedError:
                raise _mpytool.MpyError(
                    "DTR boot not available (serial only)")

    def _dispatch_ls(self, commands, is_last_group):
        args = _SUBPARSERS['ls'].parse_args(commands[:1])
        del commands[:1]
        dir_name = args.path
        # Strip trailing / except for root
        if dir_name not in (':', ':/'):
            dir_name = dir_name.rstrip('/')
        path = _parse_device_path(dir_name, 'ls')
        result = self.mpy.ls(path)
        for name, size in result:
            if size is not None:
                print(f'{_utils.format_size(size):>9} {name}')
            else:
                print(f'{"":9} {name}/')

    def _dispatch_tree(self, commands, is_last_group):
        args = _SUBPARSERS['tree'].parse_args(commands[:1])
        del commands[:1]
        dir_name = args.path
        if dir_name not in (':', ':/'):
            dir_name = dir_name.rstrip('/')
        path = _parse_device_path(dir_name, 'tree')
        tree = self.mpy.tree(path)
        self.print_tree(tree)

    def _dispatch_cat(self, commands, is_last_group):
        args = _SUBPARSERS['cat'].parse_args(list(commands))
        commands.clear()
        for file_name in args.paths:
            path = _parse_device_path(file_name, 'cat')
            self.verbose(f"CAT: {path}", 2)
            data = self.mpy.get(path)
            print(data.decode('utf-8'))

    def _dispatch_mkdir(self, commands, is_last_group):
        args = _SUBPARSERS['mkdir'].parse_args(list(commands))
        commands.clear()
        for dir_name in args.paths:
            path = _parse_device_path(dir_name, 'mkdir')
            self.verbose(f"MKDIR: {path}", 1)
            self.mpy.mkdir(path)

    def _dispatch_rm(self, commands, is_last_group):
        args = _SUBPARSERS['rm'].parse_args(list(commands))
        commands.clear()
        self.cmd_rm(*args.paths)

    def _dispatch_pwd(self, commands, is_last_group):
        flags = self._collect_flags(commands)
        _SUBPARSERS['pwd'].parse_args(flags)
        cwd = self.mpy.getcwd()
        print(cwd)

    def _dispatch_cd(self, commands, is_last_group):
        args = _SUBPARSERS['cd'].parse_args(commands[:1])
        del commands[:1]
        path = _parse_device_path(args.path, 'cd')
        self.verbose(f"CD: {path}", 2)
        self.mpy.chdir(path)

    def _dispatch_path(self, commands, is_last_group):
        args = _SUBPARSERS['path'].parse_args(list(commands))
        commands.clear()
        mode = args.mode or 'replace'
        # No arguments = show current path
        if not args.paths:
            paths = self.mpy.get_sys_path()
            print(' '.join(f':{p}' for p in paths))
            return
        # Parse paths (with : prefix)
        parsed_paths = [_parse_device_path(p, 'path') for p in args.paths]
        # Apply operation
        if mode == 'replace':
            self.mpy.set_sys_path(*parsed_paths)
            self.verbose(f"PATH set to {len(parsed_paths)} entries", 1)
        elif mode == 'first':
            self.mpy.prepend_sys_path(*parsed_paths)
            self.verbose(f"PATH prepended {len(parsed_paths)} entries", 1)
        elif mode == 'append':
            self.mpy.append_sys_path(*parsed_paths)
            self.verbose(f"PATH appended {len(parsed_paths)} entries", 1)
        elif mode == 'delete':
            self.mpy.remove_from_sys_path(*parsed_paths)
            self.verbose(f"PATH removed {len(parsed_paths)} entries", 1)

    def _dispatch_stop(self, commands, is_last_group):
        flags = self._collect_flags(commands)
        _SUBPARSERS['stop'].parse_args(flags)
        self.mpy.stop()
        self.verbose("STOP", 1)

    def _dispatch_reset(self, commands, is_last_group):
        cmd_args = self._collect_flags(commands)
        args = _SUBPARSERS['reset'].parse_args(cmd_args)
        mode = args.mode or 'soft'
        if args.timeout and mode not in ('machine', 'rts'):
            raise ParamsError('--timeout only with --machine or --rts')
        has_more = bool(commands) or not is_last_group
        reconnect = has_more if mode in ('machine', 'rts') else True
        self.cmd_reset(mode=mode, reconnect=reconnect, timeout=args.timeout)

    def _dispatch_monitor(self, commands, is_last_group):
        flags = self._collect_flags(commands)
        _SUBPARSERS['monitor'].parse_args(flags)
        self.cmd_monitor()
        commands.clear()

    def _dispatch_repl(self, commands, is_last_group):
        flags = self._collect_flags(commands)
        _SUBPARSERS['repl'].parse_args(flags)
        self.cmd_repl()
        commands.clear()

    def _dispatch_exec(self, commands, is_last_group):
        args = _SUBPARSERS['exec'].parse_args(commands[:1])
        del commands[:1]
        self.verbose(f"EXEC: {args.code}", 1)
        result = self.mpy.comm.exec(args.code)
        if result:
            print(result.decode('utf-8', 'backslashreplace'), end='')

    def _dispatch_run(self, commands, is_last_group):
        args = _SUBPARSERS['run'].parse_args(list(commands))
        commands.clear()
        if not _os.path.isfile(args.file):
            raise ParamsError(f"file not found: {args.file}")
        with open(args.file, 'rb') as f:
            code = f.read()
        self.verbose(f"RUN: {args.file} ({len(code)} bytes)", 1)
        self.mpy.comm.try_raw_paste(code, timeout=0)
        if args.monitor:
            self.cmd_monitor()

    def _dispatch_info(self, commands, is_last_group):
        flags = self._collect_flags(commands)
        _SUBPARSERS['info'].parse_args(flags)
        self.cmd_info()

    def _dispatch_flash(self, commands, is_last_group):
        args = _SUBPARSERS['flash'].parse_args(list(commands))
        commands.clear()
        if args.operation == 'read':
            if len(args.args) == 1:
                self.cmd_flash_read(args.args[0])
            elif len(args.args) == 2:
                self.cmd_flash_read(args.args[1], label=args.args[0])
            else:
                raise ParamsError('flash read: [label] file')
        elif args.operation == 'write':
            if len(args.args) == 1:
                self.cmd_flash_write(args.args[0])
            elif len(args.args) == 2:
                self.cmd_flash_write(args.args[1], label=args.args[0])
            else:
                raise ParamsError('flash write: [label] file')
        elif args.operation == 'erase':
            self.cmd_flash_erase(label=args.label, full=args.full)
        else:
            self.cmd_flash()

    def _dispatch_ota(self, commands, is_last_group):
        args = _SUBPARSERS['ota'].parse_args(commands[:1])
        del commands[:1]
        self.cmd_ota(args.firmware)

    def _dispatch_sleep(self, commands, is_last_group):
        args = _SUBPARSERS['sleep'].parse_args(commands[:1])
        del commands[:1]
        self.verbose(f"SLEEP {args.seconds}s", 1)
        _time.sleep(args.seconds)

    def _dispatch_cp(self, commands, is_last_group):
        self.cmd_cp(list(commands))
        commands.clear()

    def _dispatch_mv(self, commands, is_last_group):
        args = _SUBPARSERS['mv'].parse_args(list(commands))
        commands.clear()
        if len(args.paths) < 2:
            raise ParamsError('mv requires source and destination')
        self.cmd_mv(*args.paths)

    def _list_mounts(self):
        """List all mount points on device"""
        mounts = self.mpy.list_mounts()
        if not mounts:
            print("No filesystems found")
            return
        for fs in mounts:
            mp = fs['mount']
            if fs['fs_type']:
                print(f"{mp:12} {fs['fs_type']}")
            else:
                used_pct = (
                    fs['used'] / fs['total'] * 100
                ) if fs['total'] > 0 else 0
                print(
                    f"{mp:12} {_utils.format_size(fs['used'])} /"
                    f" {_utils.format_size(fs['total'])}"
                    f" ({used_pct:.0f}% used)")

    def _parse_mount_pairs(self, commands):
        """Parse mount directory/point pairs from commands"""
        pairs = []
        while commands and not commands[0].startswith(':'):
            local_path = commands.pop(0)
            if not _os.path.isdir(local_path):
                raise ParamsError(
                    f'mount directory not found: {local_path}')
            mount_point = '/remote'
            if commands and commands[0].startswith(':'):
                mp = commands.pop(0)[1:]
                if mp and mp.startswith('/'):
                    mount_point = mp
                else:
                    raise ParamsError(
                        'mount point must be absolute path (e.g. :/app)')
            pairs.append((local_path, mount_point))
        # Check for duplicate mount points
        mps = [mp for _, mp in pairs]
        if len(set(mps)) != len(mps):
            raise ParamsError('duplicate mount points')
        return pairs

    def _dispatch_mount(self, commands, is_last_group):
        if not commands:
            self._list_mounts()
            return
        args = _SUBPARSERS['mount'].parse_args(list(commands))
        commands.clear()
        # Initialize mpy-cross if requested
        mpy_cross = None
        if args.mpy:
            mpy_cross = MpyCross(self._log, self.verbose)
            mpy_cross.init(self.mpy.platform())
            if not mpy_cross.active:
                mpy_cross = None
        # Parse mount pairs from paths
        pairs = self._parse_mount_pairs(list(args.paths))
        # Determine first mount point for auto-chdir
        first_mount_point = None
        if not self.mpy._mounts:
            first_mount_point = pairs[0][1]
        for local_path, mount_point in pairs:
            if self.mpy.is_submount(mount_point):
                raise ParamsError(
                    f"nested mount '{mount_point}' is not allowed")
            self.mpy.mount(
                local_path, mount_point, log=self._log,
                writable=args.writable, mpy_cross=mpy_cross)
            mode = "read-write" if args.writable else "readonly"
            if mpy_cross:
                mode += ", .mpy compilation"
            self.verbose(
                f"Mounted {local_path} on {mount_point} ({mode})",
                color='green')
        self._conn = self.mpy.conn
        if first_mount_point:
            self.mpy.chdir(first_mount_point)
            self.verbose(
                f"Changed CWD to {first_mount_point}",
                color='cyan')

    def _dispatch_ln(self, commands, is_last_group):
        args = _SUBPARSERS['ln'].parse_args(list(commands))
        commands.clear()
        if len(args.paths) < 2:
            raise ParamsError('ln requires source(s) and destination')
        dst_arg = args.paths[-1]
        src_args = args.paths[:-1]
        if not dst_arg.startswith(':'):
            raise ParamsError(
                'ln destination must be device path (: prefix)')
        dst_path = dst_arg[1:]
        if not dst_path.startswith('/'):
            raise ParamsError(
                'ln destination must be absolute path (e.g. :/lib/)')
        dst_is_dir = dst_path.endswith('/')
        has_contents = any(
            s.endswith('/') or s.endswith(_os.sep)
            for s in src_args)
        if len(src_args) > 1 and not dst_is_dir:
            raise ParamsError(
                'multiple sources require directory destination'
                ' (trailing /)')
        if has_contents and not dst_is_dir:
            raise ParamsError(
                'contents source (trailing /) requires directory'
                ' destination (trailing /)')
        best_mp = None
        dst_norm = dst_path.rstrip('/')
        for p_mid, p_mp, _, _ in self.mpy._mounts:
            if p_mid is None:
                continue
            mp_norm = p_mp.rstrip('/')
            if dst_norm == mp_norm or dst_norm.startswith(mp_norm + '/'):
                if best_mp is None or len(p_mp) > len(best_mp):
                    best_mp = p_mp
        if best_mp is None:
            raise ParamsError(
                'ln requires an active mount (use mount first)')
        mp_prefix = best_mp.rstrip('/')
        dst_rel = dst_path[len(mp_prefix):].strip('/')
        for src in src_args:
            is_contents = src.endswith('/') or src.endswith(_os.sep)
            local_path = src.rstrip('/').rstrip(_os.sep)
            if not _os.path.exists(local_path):
                raise ParamsError(f"source not found: '{local_path}'")
            if dst_is_dir and not is_contents:
                basename = _os.path.basename(local_path)
                subpath = dst_rel + '/' + basename if dst_rel else basename
            else:
                subpath = dst_rel
            self.mpy.add_submount(best_mp, subpath, local_path)
            self.verbose(
                f"Linked {local_path} -> {best_mp}/{subpath}",
                color='green')

    def _dispatch_speedtest(self, commands, is_last_group):
        flags = self._collect_flags(commands)
        _SUBPARSERS['speedtest'].parse_args(flags)
        from mpytool.speedtest import speedtest
        self.verbose("SPEEDTEST", 1)
        speedtest(self.mpy.comm, self._log)

    def _dispatch_paths(self, commands, is_last_group):
        # For shell completion - list files on device
        dir_name = commands.pop(0) if commands else ':'
        path = _parse_device_path(dir_name, '_paths')
        try:
            entries = self.mpy.ls(path)
        except (_mpytool.DirNotFound, _mpytool.MpyError):
            return
        for name, size in entries:
            print(name + '/' if size is None else name)

    def _dispatch_ports(self, commands, is_last_group):
        # For shell completion - list available serial ports (no device needed)
        for port in _utils.detect_serial_ports():
            print(port)

    def _dispatch_commands(self, commands, is_last_group):
        # For shell completion - list commands with descriptions
        # Output: name:description (ZSH _describe compatible)
        for name in _CMD_ORDER:
            if name in _SUBPARSERS:
                desc = _SUBPARSERS[name].description or ''
                print(f"{name}:{desc}")

    def _dispatch_options(self, commands, is_last_group):
        # For shell completion - list options for a command
        # Output: option:description:argtype (argtype empty = flag)
        # Without command: global; with command: command-specific
        cmd_name = commands.pop(0) if commands else ''
        if cmd_name:
            if cmd_name not in _SUBPARSERS:
                return
            parser = _SUBPARSERS[cmd_name]
        else:
            parser = _MAIN_PARSER
        for action in parser._actions:
            if not action.option_strings:
                continue  # Skip positional arguments
            if '--help' in action.option_strings:
                continue  # Shell handles --help
            if '--version' in action.option_strings:
                continue  # Shell handles --version
            # Use longest option string (--foo preferred over -f)
            opt = max(action.option_strings, key=len)
            desc = action.help or ''
            # Determine argument type (empty = flag/no argument)
            argtype = ''
            if not isinstance(action, (
                    _argparse._StoreTrueAction, _argparse._StoreFalseAction,
                    _argparse._StoreConstAction, _argparse._CountAction)):
                # Option takes an argument - determine type
                argtype = (action.metavar or action.dest or '').lower()
            print(f"{opt}:{desc}:{argtype}")

    def _dispatch_args(self, commands, is_last_group):
        # For shell completion - list positional arguments
        # Output: type:nargs:description
        # nargs: 1 = one, ? = optional, + = one+, * = zero+
        cmd_name = commands.pop(0) if commands else ''
        if cmd_name not in _SUBPARSERS:
            return
        parser = _SUBPARSERS[cmd_name]
        for action in parser._actions:
            if action.option_strings:
                continue  # Skip options, only positional arguments
            argtype = (action.metavar or action.dest or '').lower()
            nargs = action.nargs if action.nargs else '1'
            desc = action.help or ''
            print(f"{argtype}:{nargs}:{desc}")

    _COMMANDS = frozenset({
        'ls', 'tree', 'cat', 'mkdir', 'rm', 'pwd', 'cd', 'path',
        'reset', 'stop', 'monitor', 'repl', 'exec', 'run', 'info', 'flash',
        'ota', 'sleep', 'cp', 'mv', 'mount', 'ln', 'speedtest',
        '_paths', '_ports', '_commands', '_options', '_args',
    })

    def process_commands(self, commands, is_last_group=False):
        while commands:
            command = commands.pop(0)
            if command not in self._COMMANDS:
                raise ParamsError(f"unknown command: '{command}'")
            dispatch = getattr(self, f'_dispatch_{command.lstrip("_")}')
            dispatch(commands, is_last_group)
        try:
            self.mpy.comm.exit_raw_repl()
        except _mpytool.ConnError:
            pass  # connection already lost


def _build_commands_help():
    """Build commands help from subparsers descriptions."""
    lines = ["Commands (use '<command> --help' for details):"]
    for name in _CMD_ORDER:
        if name in _SUBPARSERS:
            desc = _SUBPARSERS[name].description or ''
            lines.append(f"  {name:12} {desc}")
    lines.append("")
    lines.append("Use -- to chain commands:")
    lines.append("  mpytool cp main.py : -- reset -- monitor")
    return '\n'.join(lines)


def _run_commands(mpy_tool, command_groups, with_progress=True):
    """Execute command groups with optional batch progress tracking"""
    if not with_progress:
        for i, commands in enumerate(command_groups):
            is_last = (i == len(command_groups) - 1)
            mpy_tool.process_commands(commands, is_last_group=is_last)
        return
    i = 0
    while i < len(command_groups):
        result = mpy_tool.count_files_for_command(command_groups[i])
        is_copy, count, src_paths, dst_paths = result
        if not is_copy:
            is_last = (i == len(command_groups) - 1)
            mpy_tool.process_commands(command_groups[i], is_last_group=is_last)
            i += 1
            continue
        batch_total = count
        all_src_paths = src_paths
        all_dst_paths = dst_paths
        batch_start = i
        j = i + 1
        while j < len(command_groups):
            result = mpy_tool.count_files_for_command(command_groups[j])
            is_copy_j, count_j, src_paths_j, dst_paths_j = result
            if not is_copy_j:
                break
            batch_total += count_j
            all_src_paths.extend(src_paths_j)
            all_dst_paths.extend(dst_paths_j)
            j += 1
        max_src_len = max(len(p) for p in all_src_paths) if all_src_paths else 0
        max_dst_len = max(len(p) for p in all_dst_paths) if all_dst_paths else 0
        mpy_tool.print_transfer_info()
        mpy_tool.set_batch_progress(batch_total, max_src_len, max_dst_len)
        for k in range(batch_start, j):
            is_last = (k == j - 1) and (j == len(command_groups))
            mpy_tool.process_commands(command_groups[k], is_last_group=is_last)
        mpy_tool.print_copy_summary()
        mpy_tool.reset_batch_progress()
        i = j


def _parse_chunk_size(value):
    """Parse chunk size value (e.g., '1K', '2K', '4096')"""
    valid = {512, 1024, 2048, 4096, 8192, 16384, 32768}
    val = value.upper()
    if val.endswith('K'):
        try:
            num = int(val[:-1]) * 1024
        except ValueError:
            raise _argparse.ArgumentTypeError(f"invalid chunk size: {value}")
    else:
        try:
            num = int(value)
        except ValueError:
            raise _argparse.ArgumentTypeError(f"invalid chunk size: {value}")
    if num not in valid:
        parts = []
        for v in sorted(valid):
            parts.append(f'{v//1024}K' if v >= 1024 else str(v))
        valid_str = ', '.join(parts)
        raise _argparse.ArgumentTypeError(
            f"chunk size must be one of: {valid_str}")
    return num


def _mount_auto_repl(command_groups):
    """Append repl to command groups if mount is used without terminal command.

    mount requires mpytool to stay alive (PC handles FS requests).
    If the last command after mount is not repl or monitor, auto-append repl.
    """
    # mount with args = real mount (needs repl to stay alive)
    # mount without args = listing only (no repl needed)
    has_mount = any(
        group[0] == 'mount' and len(group) > 1
        for group in command_groups if group)
    if not has_mount:
        return
    # Check first item (command name) of last group
    last_group = command_groups[-1] if command_groups else []
    last_cmd = last_group[0] if last_group else None
    if last_cmd not in ('repl', 'monitor'):
        command_groups.append(['repl'])


def _build_main_parser():
    """Build the main argument parser."""
    _description = _about["Summary"] if _about else None
    parser = _argparse.ArgumentParser(
        description=_description,
        formatter_class=_argparse.RawTextHelpFormatter,
        epilog=_build_commands_help())
    parser.add_argument(
        "-V", "--version", action='version', version=_VERSION_STR)
    parser.add_argument('-p', '--port', help="serial port")
    parser.add_argument('-a', '--address', help="network address")
    parser.add_argument(
        '-b', '--baud', type=int, default=115200, help="baud rate")
    parser.add_argument(
        '-d', '--debug', default=0, action='count', help='debug level')
    parser.add_argument(
        '-v', '--verbose', action='store_true', help='verbose output')
    parser.add_argument(
        '-q', '--quiet', action='store_true', help='quiet mode')
    parser.add_argument(
        "-e", "--exclude", type=str, action='append', dest='exclude',
        help='exclude pattern (wildcards: *, ?)')
    parser.add_argument(
        '-f', '--force', action='store_true', help='force overwrite')
    parser.add_argument(
        '-z', '--compress', action='store_true', default=None,
        help='force compression')
    parser.add_argument(
        '-Z', '--no-compress', action='store_true', help='disable compression')
    parser.add_argument(
        '-c', '--chunk-size', type=_parse_chunk_size, metavar='SIZE',
        help='chunk size (512, 1K-32K, auto)')
    parser.add_argument('commands', nargs=_argparse.REMAINDER, help='commands')
    return parser


_MAIN_PARSER = _build_main_parser()


def main():
    """Main"""
    args = _MAIN_PARSER.parse_args()
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
    # Determine compression setting: None=auto, True=force, False=disable
    compress = None
    if args.no_compress:
        compress = False
    elif args.compress:
        compress = True
    # Create MpyTool with lazy connection initialization
    mpy_tool = MpyTool(
        log=log, verbose=log, exclude_dirs=args.exclude,
        force=args.force, compress=compress, chunk_size=args.chunk_size,
        port=args.port, address=args.address, baudrate=args.baud)
    command_groups = _utils.split_commands(args.commands)
    # Auto-REPL for mount: if mount is used and last command is not repl/monitor
    _mount_auto_repl(command_groups)
    try:
        with_progress = args.verbose >= 1
        _run_commands(mpy_tool, command_groups, with_progress=with_progress)
    except (_mpytool.MpyError, _mpytool.ConnError, _mpytool.Timeout) as err:
        log.error(err)
    except KeyboardInterrupt:
        # Clear partial progress line and show clean message
        log.verbose('Interrupted', level=0, overwrite=True)


if __name__ == '__main__':
    main()
