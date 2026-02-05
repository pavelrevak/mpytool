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

try:
    _about = _metadata.metadata("mpytool")
except _metadata.PackageNotFoundError:
    _about = None


class ParamsError(_mpytool.MpyError):
    """Invalid command parameters"""


def _join_remote_path(base, name):
    """Join remote path components (handles empty string and '/' correctly)"""
    if not name:
        return base
    if base == '/':
        return '/' + name
    elif base:
        return base + '/' + name
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
        raise ParamsError(f'{cmd_name} requires device path (: prefix): {path}')
    return path[1:]


class MpyTool():
    SPACE = '   '
    BRANCH = '│  '
    TEE = '├─ '
    LAST = '└─ '

    def __init__(
            self, conn, log=None, verbose=None, exclude_dirs=None,
            force=False, compress=None, chunk_size=None):
        self._conn = conn
        self._log = log if log is not None else SimpleColorLogger()
        self._verbose_out = verbose  # None = no verbose output (API mode)
        self._exclude_dirs = {'.*', '*.pyc'}
        if exclude_dirs:
            self._exclude_dirs.update(exclude_dirs)
        self._mpy = _mpytool.Mpy(conn, log=self._log, chunk_size=chunk_size)
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
        self._stats_wire_bytes = 0  # Actual bytes sent over wire (with encoding)
        self._stats_transferred_files = 0
        self._stats_start_time = None
        # Remote file info cache for batch operations
        self._remote_file_cache = {}  # {path: (size, hash) or None}

    def _is_excluded(self, name):
        """Check if name matches any exclude pattern (supports wildcards)"""
        for pattern in self._exclude_dirs:
            if _fnmatch.fnmatch(name, pattern):
                return True
        return False

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
        chunk = self._mpy._detect_chunk_size()
        chunk_str = f"{chunk // 1024}K" if chunk >= 1024 else str(chunk)
        compress = self._mpy._detect_deflate() if self._compress is None else self._compress
        compress_str = "on" if compress else "off"
        self.verbose(f"COPY (chunk: {chunk_str}, compress: {compress_str})", 1)

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

    # Encoding info strings for alignment: (base64), (compressed), (base64, compressed), (unchanged)
    _ENC_WIDTH = 22  # Length of longest: "  (base64, compressed)"

    def _format_encoding_info(self, encodings, pad=False):
        """Format encoding info: (base64), (compressed), (base64, compressed)"""
        if not encodings or encodings == {'raw'}:
            return " " * self._ENC_WIDTH if pad else ""
        types = sorted(e for e in encodings if e != 'raw')
        if not types:
            return " " * self._ENC_WIDTH if pad else ""
        info = f"  ({', '.join(types)})"
        if pad:
            return f"{info:<{self._ENC_WIDTH}}"
        return info

    def _format_line(self, status, total, encodings=None):
        """Format progress/skip line: [2/5] 100% 24.1K source -> dest (base64)"""
        size_str = self.format_size(total)
        multi = self._progress_total_files > 1
        if multi:
            width = len(str(self._progress_total_files))
            prefix = f"[{self._progress_current_file:>{width}}/{self._progress_total_files}]"
        else:
            prefix = ""
        src_w = max(len(self._progress_src), self._progress_max_src_len)
        dst_w = max(len(self._progress_dst), self._progress_max_dst_len)
        enc = self._format_encoding_info(encodings, pad=multi) if encodings else (" " * self._ENC_WIDTH if multi else "")
        return f"{prefix:>7} {status} {size_str:>5} {self._progress_src:<{src_w}} -> {self._progress_dst:<{dst_w}}{enc}"

    def _format_progress_line(self, percent, total, encodings=None):
        return self._format_line(f"{percent:3d}%", total, encodings)

    def _format_skip_line(self, total):
        return self._format_line("skip", total, {'unchanged'})

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

    def _progress_complete(self, total, encodings=None):
        """Mark current file as complete"""
        line = self._format_progress_line(100, total, encodings)
        if self._is_debug:
            # Already printed with newline in callback
            pass
        else:
            self.verbose(line, color='cyan', overwrite=True)

    def _set_progress_info(self, src, dst, is_src_remote, is_dst_remote):
        """Set progress source and destination paths"""
        if is_src_remote:
            self._progress_src = ':' + src
        else:
            self._progress_src = self._format_local_path(src)
        if is_dst_remote:
            self._progress_dst = ':' + ('/' + dst if not dst.startswith('/') else dst)
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
            paths.extend(self._format_local_path(_os.path.join(root, f)) for f in files)
        return paths

    def _prefetch_remote_info(self, dst_files):
        """Prefetch remote file info (size and hash) for multiple files

        Arguments:
            dst_files: dict {remote_path: local_size} - sizes used to skip hash if mismatch

        Uses batch call to reduce round-trips. Results are cached in _remote_file_cache.
        """
        if self._force or not dst_files:
            return
        files_to_fetch = {p: s for p, s in dst_files.items() if p not in self._remote_file_cache}
        if not files_to_fetch:
            return
        self.verbose(f"Checking {len(files_to_fetch)} files...", 2)
        result = self._mpy.fileinfo(files_to_fetch)
        if result is None:
            # hashlib not available - mark all as needing update
            for path in files_to_fetch:
                self._remote_file_cache[path] = None
        else:
            self._remote_file_cache.update(result)

    def _collect_dst_files(self, src_path, dst_path, add_src_basename=True):
        """Collect destination paths and local sizes for a local->remote copy operation

        Arguments:
            src_path: local source path (file or directory)
            dst_path: remote destination base path
            add_src_basename: if True, add source basename to dst_path (matching _put_dir behavior)

        Returns:
            dict {remote_path: local_file_size}
        """
        files = {}
        if _os.path.isfile(src_path):
            # For files, add basename if dst_path ends with /
            basename = _os.path.basename(src_path)
            if basename and not _os.path.basename(dst_path):
                dst_path = _os.path.join(dst_path, basename)
            files[dst_path] = _os.path.getsize(src_path)
        elif _os.path.isdir(src_path):
            # For directories, mimic _put_dir behavior
            if add_src_basename:
                basename = _os.path.basename(_os.path.abspath(src_path))
                if basename:
                    dst_path = _os.path.join(dst_path, basename)
            for root, dirs, filenames in _os.walk(src_path, topdown=True):
                dirs[:] = [d for d in dirs if not self._is_excluded(d)]
                filenames = [f for f in filenames if not self._is_excluded(f)]
                rel_path = _os.path.relpath(root, src_path)
                if rel_path == '.':
                    rel_path = ''
                for file_name in filenames:
                    spath = _os.path.join(root, file_name)
                    dpath = _os.path.join(dst_path, rel_path, file_name) if rel_path else _os.path.join(dst_path, file_name)
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
                return True  # Size matched but hash wasn't computed (shouldn't happen with prefetch)
            local_hash = _hashlib.sha256(local_data).digest()
            return local_hash != remote_hash
        # Fallback to individual calls (for single file operations)
        remote_size = self._mpy.stat(remote_path)
        if remote_size is None or remote_size < 0:
            return True  # File doesn't exist or is a directory
        local_size = len(local_data)
        if local_size != remote_size:
            return True  # Different size
        # Sizes match - check hash
        local_hash = _hashlib.sha256(local_data).digest()
        remote_hash = self._mpy.hashfile(remote_path)
        if remote_hash is None:
            return True  # hashlib not available on device
        return local_hash != remote_hash

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
                    paths.extend(self._collect_remote_paths(src_path))
                else:
                    paths.extend(self._collect_local_paths(src_path))
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
                src_path = ((src[1:] or '/') if src_is_remote else src).rstrip('/') or '/'
                copy_contents = src.endswith('/')
                if dest_is_remote and not src_is_remote:
                    # local -> remote: reuse _collect_dst_files
                    if _os.path.exists(src_path):
                        files = self._collect_dst_files(src_path, dest_path.rstrip('/') or '/', not copy_contents)
                        dst_paths.extend(':' + p for p in files)
                elif not dest_is_remote and src_is_remote:
                    # remote -> local
                    dst_paths.extend(self._collect_remote_to_local_dst(src_path, dest_path, dest_is_dir, copy_contents))
                elif dest_is_remote and src_is_remote:
                    # remote -> remote (file only)
                    stat = self._mpy.stat(src_path)
                    if stat is not None and stat >= 0:
                        dst = _join_remote_path(dest_path, _remote_basename(src_path)) if dest_is_dir else dest_path
                        dst_paths.append(':' + dst)
            return dst_paths
        return []

    def _collect_remote_to_local_dst(self, src_path, dest_path, dest_is_dir, copy_contents):
        """Collect destination paths for remote->local copy"""
        stat = self._mpy.stat(src_path)
        if stat is None:
            return []
        base_dst = dest_path.rstrip('/') or '.'
        if dest_is_dir and not copy_contents and src_path != '/':
            base_dst = _os.path.join(base_dst, _remote_basename(src_path))
        if stat >= 0:  # file
            if _os.path.isdir(base_dst) or dest_is_dir:
                return [self._format_local_path(_os.path.join(base_dst, _remote_basename(src_path)))]
            return [self._format_local_path(base_dst)]
        return self._collect_remote_dir_dst(src_path, base_dst)

    def _collect_remote_dir_dst(self, src_path, base_dst):
        """Collect local destination paths for remote directory download"""
        paths = []
        for name, size in self._mpy.ls(src_path):
            entry_src = src_path.rstrip('/') + '/' + name
            entry_dst = _os.path.join(base_dst, name)
            if size is None:  # directory
                paths.extend(self._collect_remote_dir_dst(entry_src, entry_dst))
            else:  # file
                paths.append(self._format_local_path(entry_dst))
        return paths

    def count_files_for_command(self, commands):
        """Count files that would be transferred by cp/put command.
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
        parts.append(f"{self.format_size(transferred).strip()}")
        if elapsed > 0:
            speed = transferred / elapsed
            parts.append(f"{self.format_size(speed).strip()}/s")
        parts.append(f"{elapsed:.1f}s")
        # Combined speedup: total file size vs actual wire bytes
        # Includes savings from: skipped files, base64 encoding, compression
        if wire > 0 and total > wire:
            speedup = total / wire
            parts.append(f"speedup {speedup:.1f}x")
        summary = "  ".join(parts)
        if self._skipped_files > 0:
            file_info = f"{self._stats_transferred_files} transferred, {self._skipped_files} skipped"
        else:
            file_info = f"{total_files} files"
        self.verbose(f"  {summary}  ({file_info})", color='green')

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
        if sub_tree is not None and name != '/':
            sufix = '/'
        # For root, display './' only for empty path (CWD)
        display_name = '.' if first and name in ('', '.') else name
        line = ''
        if print_size:
            line += f'{cls.format_size(size):>9} '
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
        """Upload file data to device with stats tracking and progress display"""
        file_size = len(data)
        self._stats_total_bytes += file_size
        if not self._file_needs_update(data, dst_path):
            self._skipped_files += 1
            if show_progress and self._verbose >= 1:
                self._progress_current_file += 1
                self._set_progress_info(src_path, dst_path, False, True)
                self.verbose(self._format_skip_line(file_size), color='yellow')
            return False  # skipped
        self._stats_transferred_bytes += file_size
        self._stats_transferred_files += 1
        if show_progress and self._verbose >= 1:
            self._progress_current_file += 1
            self._set_progress_info(src_path, dst_path, False, True)
            encodings, wire = self._mpy.put(data, dst_path, self._progress_callback, self._compress)
            self._stats_wire_bytes += wire
            self._progress_complete(file_size, encodings)
        else:
            _, wire = self._mpy.put(data, dst_path, compress=self._compress)
            self._stats_wire_bytes += wire
        return True  # uploaded

    def _put_dir(self, src_path, dst_path, show_progress=True, target_name=None):
        """Upload directory to device

        Arguments:
            src_path: local source directory
            dst_path: remote destination parent directory
            show_progress: show progress bar
            target_name: if set, use this as directory name instead of src basename
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
            dirs[:] = [d for d in dirs if not self._is_excluded(d)]
            files = [f for f in files if not self._is_excluded(f)]
            if not files:
                continue
            rel_path = _os.path.relpath(path, src_path)
            rel_path = _os.path.join(dst_path, '' if rel_path == '.' else rel_path)
            if rel_path and rel_path not in created_dirs:
                self.verbose(f'MKDIR: {rel_path}', 2)
                self._mpy.mkdir(rel_path)
                created_dirs.add(rel_path)
            for file_name in files:
                spath = _os.path.join(path, file_name)
                with open(spath, 'rb') as f:
                    self._upload_file(f.read(), spath, _os.path.join(rel_path, file_name), show_progress)

    def _put_file(self, src_path, dst_path, show_progress=True):
        basename = _os.path.basename(src_path)
        if basename and not _os.path.basename(dst_path):
            dst_path = _os.path.join(dst_path, basename)
        self.verbose(f"PUT FILE: {src_path} -> {dst_path}", 2)
        with open(src_path, 'rb') as f:
            data = f.read()
        parent = _os.path.dirname(dst_path)
        if parent:
            stat = self._mpy.stat(parent)
            if stat is None:
                self._mpy.mkdir(parent)
            elif stat >= 0:
                raise _mpytool.MpyError(f'Error creating file under file: {parent}')
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
            data = self._mpy.get(src_path, self._progress_callback)
            self._progress_complete(len(data))
        else:
            data = self._mpy.get(src_path)
        file_size = len(data)
        self._stats_total_bytes += file_size
        self._stats_transferred_bytes += file_size
        self._stats_transferred_files += 1
        with open(dst_path, 'wb') as dst_file:
            dst_file.write(data)

    def _get_dir(self, src_path, dst_path, copy_contents=False, show_progress=True):
        """Download directory from device"""
        if not copy_contents:
            basename = _remote_basename(src_path)
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
        """Upload local file/dir to device

        Path semantics:
        - dst_is_dir=True: add source basename to dst_path (unless copy_contents)
        - dst_is_dir=False: dst_path is the target name (rename)
        - copy_contents (src ends with /): copy contents, not directory itself
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
                        self._put_file(item_src, _join_remote_path(dst_path, item))
            elif dst_is_dir:
                # Copy directory to destination directory (_put_dir adds basename)
                self._put_dir(src_path, dst_path)
            else:
                # Rename: copy directory with new name
                parent = _os.path.dirname(dst_path)
                target_name = _os.path.basename(dst_path)
                self._put_dir(src_path, parent, target_name=target_name)
        else:
            # File: add basename if dst_is_dir
            if dst_is_dir:
                dst_path = _join_remote_path(dst_path, _os.path.basename(src_path))
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
        stat = self._mpy.stat(src_path)
        if stat is None:
            raise ParamsError(f'Source not found on device: {src_path}')
        if stat == -1:
            raise ParamsError('Remote-to-remote directory copy not supported yet')
        if dst_is_dir:
            dst_path = _join_remote_path(dst_path, _remote_basename(src_path))
        self.verbose(f"COPY: {src_path} -> {dst_path}", 2)
        if self._verbose >= 1:
            self._progress_current_file += 1
            self._set_progress_info(src_path, dst_path, True, True)
            data = self._mpy.get(src_path, self._progress_callback)
            encodings, wire = self._mpy.put(data, dst_path, compress=self._compress)
            self._stats_wire_bytes += wire
            self._progress_complete(len(data), encodings)
        else:
            data = self._mpy.get(src_path)
            _, wire = self._mpy.put(data, dst_path, compress=self._compress)
            self._stats_wire_bytes += wire
        file_size = len(data)
        self._stats_total_bytes += file_size
        self._stats_transferred_bytes += file_size
        self._stats_transferred_files += 1

    def cmd_cp(self, *args):
        """Copy files between local and device"""
        # Parse flags: -f/--force, -z/--compress, -Z/--no-compress
        args = list(args)
        force = None
        compress = None  # None = use global setting
        filtered_args = []
        for a in args:
            if a == '--force':
                force = True
            elif a == '--compress':
                compress = True
            elif a == '--no-compress':
                compress = False
            elif a.startswith('-') and not a.startswith('--') and len(a) > 1:
                # Handle combined short flags like -fz, -fZ
                flags = a[1:]
                if 'f' in flags:
                    force = True
                if 'z' in flags:
                    compress = True
                if 'Z' in flags:
                    compress = False
                remaining = flags.replace('f', '').replace('z', '').replace('Z', '')
                if remaining:
                    filtered_args.append('-' + remaining)
            else:
                filtered_args.append(a)
        args = filtered_args
        if len(args) < 2:
            raise ParamsError('cp requires source and destination')
        # Save and set flags for this command
        saved_force = self._force
        saved_compress = self._compress
        if force is not None:
            self._force = force
        if compress is not None:
            self._compress = compress
        try:
            self._cmd_cp_impl(args)
        finally:
            self._force = saved_force
            self._compress = saved_compress

    def _cmd_cp_impl(self, args):
        sources = list(args[:-1])
        dest = args[-1]
        dest_is_remote = dest.startswith(':')
        dest_path = dest[1:] if dest_is_remote else dest
        # Determine if destination is a directory:
        # - Remote: '' (CWD) or '/' (root) or ends with '/'
        # - Local: ends with '/' or exists as directory
        if dest_is_remote:
            dest_is_dir = dest_path == '' or dest_path == '/' or dest_path.endswith('/')
        else:
            dest_is_dir = dest_path.endswith('/') or _os.path.isdir(dest_path)
        # Check if any source copies contents (trailing slash) or multiple sources
        has_multi_source = len(sources) > 1
        has_contents_copy = any(s.rstrip('/') != s for s in sources)  # any source has trailing /
        if (has_multi_source or has_contents_copy) and not dest_is_dir:
            raise ParamsError('multiple sources or directory contents require destination directory (ending with /)')
        if self._verbose >= 1 and not self._batch_mode:
            total_files = 0
            for src in sources:
                src_is_remote = src.startswith(':')
                src_path = src[1:] if src_is_remote else src
                if not src_path:
                    src_path = '/'
                src_path_clean = src_path.rstrip('/') or '/'
                if src_is_remote:
                    total_files += len(self._collect_remote_paths(src_path_clean))
                else:
                    total_files += len(self._collect_local_paths(src_path_clean))
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
                        all_dst_files.update(self._collect_dst_files(src_path, base_path, add_basename))
            if all_dst_files and not self._batch_mode:
                self._progress_max_dst_len = max(len(':' + p) for p in all_dst_files)
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
        dest_is_dir = dest_path == '' or dest_path == '/' or dest_path.endswith('/')
        if len(sources) > 1 and not dest_is_dir:
            raise ParamsError('multiple sources require destination directory (ending with /)')
        self._mpy.import_module('os')
        for src in sources:
            src_path = _parse_device_path(src, 'mv')
            stat = self._mpy.stat(src_path)
            if stat is None:
                raise ParamsError(f'Source not found on device: {src_path}')
            if dest_is_dir:
                # Preserve '/' as root, strip trailing slash from others
                dst_dir = '/' if dest_path == '/' else dest_path.rstrip('/')
                if dst_dir and dst_dir != '/' and self._mpy.stat(dst_dir) is None:
                    self._mpy.mkdir(dst_dir)
                final_dest = _join_remote_path(dst_dir, _remote_basename(src_path))
            else:
                final_dest = dest_path
            self.verbose(f"MV: {src_path} -> {final_dest}", 1)
            self._mpy.rename(src_path, final_dest)

    def cmd_rm(self, *file_names):
        """Delete files/directories on device"""
        for file_name in file_names:
            raw_path = _parse_device_path(file_name, 'rm')
            contents_only = raw_path.endswith('/') or raw_path == ''
            # ':' = CWD contents, ':/' = root, ':/path' = path, ':path/' = path contents
            if raw_path == '':
                path = ''  # CWD
            elif raw_path == '/':
                path = '/'  # root
            else:
                path = raw_path.rstrip('/') if contents_only else raw_path
            if contents_only:
                self.verbose(f"RM contents: {path or 'CWD'}", 1)
                entries = self._mpy.ls(path)
                for name, size in entries:
                    entry_path = _join_remote_path(path, name)
                    self.verbose(f"  {entry_path}", 1)
                    self._mpy.delete(entry_path)
            else:
                self.verbose(f"RM: {path}", 1)
                self._mpy.delete(path)

    def cmd_monitor(self):
        self.verbose("MONITOR (Ctrl+C to stop)", 1)
        try:
            while True:
                line = self._conn.read_line()
                line = line.decode('utf-8', 'backslashreplace')
                print(line)
        except KeyboardInterrupt:
            self.verbose('', level=0, overwrite=True)  # newline after ^C
        except (_mpytool.ConnError, OSError) as err:
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

    @staticmethod
    def format_size(size):
        """Format size in bytes to human readable format (like ls -h)"""
        if size < 1024:
            return f"{int(size)}B"
        for unit in ('K', 'M', 'G', 'T'):
            size /= 1024
            if size < 10:
                return f"{size:.2f}{unit}"
            if size < 100:
                return f"{size:.1f}{unit}"
            if size < 1024 or unit == 'T':
                return f"{size:.0f}{unit}"
        return f"{size:.0f}T"

    def cmd_ota(self, firmware_path):
        """OTA firmware update from local .app-bin file"""
        self.verbose("OTA UPDATE", 1)
        if not _os.path.isfile(firmware_path):
            raise ParamsError(f"Firmware file not found: {firmware_path}")

        with open(firmware_path, 'rb') as f:
            firmware = f.read()

        fw_size = len(firmware)
        self.verbose(f"  Firmware: {self.format_size(fw_size)}", 1)
        info = self._mpy.partitions()
        if not info['next_ota']:
            raise _mpytool.MpyError("OTA not available (no OTA partitions)")

        self.verbose(f"  Target: {info['next_ota']} ({self.format_size(info['next_ota_size'])})", 1)
        use_compress = self._mpy._detect_deflate()
        chunk_size = self._mpy._detect_chunk_size()
        chunk_str = f"{chunk_size // 1024}K" if chunk_size >= 1024 else str(chunk_size)
        self.verbose(f"  Writing (chunk: {chunk_str}, compress: {'on' if use_compress else 'off'})...", 1)
        start_time = _time.time()

        def progress_callback(transferred, total, wire_bytes):
            if self._verbose >= 1:
                pct = transferred * 100 // total
                elapsed = _time.time() - start_time
                speed = transferred / elapsed / 1024 if elapsed > 0 else 0
                line = f"  Writing: {pct:3d}% {self.format_size(transferred):>6} / {self.format_size(total)}  {speed:.1f} KB/s"
                self.verbose(line, color='cyan', end='', overwrite=True)

        result = self._mpy.ota_write(firmware, progress_callback, self._compress)
        elapsed = _time.time() - start_time
        speed = fw_size / elapsed / 1024 if elapsed > 0 else 0
        ratio = fw_size / result['wire_bytes'] if result['wire_bytes'] > 0 else 1
        self.verbose(
            f"  Writing: 100% {self.format_size(fw_size):>6}  {elapsed:.1f}s  {speed:.1f} KB/s  ratio {ratio:.2f}x",
            color='cyan', overwrite=True
        )

        self.verbose(f"  OTA complete! Use 'mreset' to boot into new firmware.", 1, color='green')

    def cmd_flash(self):
        """Show flash information (auto-detect platform)"""
        self.verbose("FLASH", 2)
        platform = self._mpy.platform()['platform']

        if platform == 'rp2':
            self._cmd_flash_rp2()
        elif platform == 'esp32':
            self._cmd_flash_esp32()
        else:
            raise _mpytool.MpyError(f"Flash info not supported for platform: {platform}")

    def _cmd_flash_rp2(self):
        """Show RP2 flash information"""
        info = self._mpy.flash_info()
        print(f"Platform:    RP2")
        print(f"Flash size:  {self.format_size(info['size'])}")
        print(f"Block size:  {info['block_size']} bytes")
        print(f"Block count: {info['block_count']}")
        fs_line = f"Filesystem:  {info['filesystem']}"
        # For FAT, show cluster size if detected from magic
        if info.get('fs_block_size'):
            fs_line += f" (cluster: {self.format_size(info['fs_block_size'])})"
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
                self.verbose(
                    f"  {prefix}: {pct:.0f}% {self.format_size(transferred)} / {self.format_size(total)}",
                    color='cyan', end='', overwrite=True)
        return progress

    def cmd_flash_read(self, dest_path, label=None):
        """Read flash/partition content to file"""
        if label:
            self.verbose(f"FLASH READ {label} -> {dest_path}", 1)
        else:
            self.verbose(f"FLASH READ -> {dest_path}", 1)

        data = self._mpy.flash_read(label=label, progress_callback=self._make_progress("reading", label))

        if self._verbose >= 1:
            print()  # newline after progress

        with open(dest_path, 'wb') as f:
            f.write(data)

        self.verbose(f"  saved {self.format_size(len(data))} to {dest_path}", 1, color='green')

    def cmd_flash_write(self, src_path, label=None):
        """Write file content to flash/partition"""
        if label:
            self.verbose(f"FLASH WRITE {src_path} -> {label}", 1)
        else:
            self.verbose(f"FLASH WRITE {src_path}", 1)

        with open(src_path, 'rb') as f:
            data = f.read()

        result = self._mpy.flash_write(
            data, label=label,
            progress_callback=self._make_progress("writing", label),
            compress=self._compress)

        if self._verbose >= 1:
            print()  # newline after progress

        target = label or "flash"
        comp_info = " (compressed)" if result.get('compressed') else ""
        self.verbose(f"  wrote {self.format_size(result['written'])} to {target}{comp_info}", 1, color='green')

    def cmd_flash_erase(self, label=None, full=False):
        """Erase flash/partition (filesystem reset)"""
        mode = "full" if full else "quick"
        if label:
            self.verbose(f"FLASH ERASE {label} ({mode})", 1)
        else:
            self.verbose(f"FLASH ERASE ({mode})", 1)

        result = self._mpy.flash_erase(label=label, full=full, progress_callback=self._make_progress("erasing", label))

        if self._verbose >= 1:
            print()  # newline after progress

        target = label or "flash"
        self.verbose(f"  erased {self.format_size(result['erased'])} from {target}", 1, color='green')
        if not label:
            self.verbose("  filesystem will be recreated on next boot", 1, color='yellow')

    def _cmd_flash_esp32(self):
        """List ESP32 partitions"""
        info = self._mpy.partitions()
        print(f"{'Label':<12} {'Type':<8} {'Subtype':<10} {'Address':>10} {'Size':>10} "
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
                block_str = self.format_size(p['fs_block_size'])
            # Filesystem column
            fs_info = ''
            if p.get('filesystem'):
                fs_info = p['filesystem']
                # For FAT, append cluster size
                if p.get('fs_cluster_size'):
                    fs_info += f" ({self.format_size(p['fs_cluster_size'])})"
            print(f"{p['label']:<12} {p['type_name']:<8} {p['subtype_name']:<10} "
                  f"{p['offset']:>#10x} {self.format_size(p['size']):>10} "
                  f"{block_str:>8} {fs_info:<12} {', '.join(flags)}")

        if info['boot']:
            print(f"\nBoot partition: {info['boot']}")
        if info['next_ota']:
            print(f"Next OTA:       {info['next_ota']}")

    def cmd_info(self):
        self.verbose("INFO", 2)
        plat = self._mpy.platform()
        print(f"Platform:    {plat['platform']}")
        print(f"Version:     {plat['version']}")
        print(f"Impl:        {plat['impl']}")
        if plat['machine']:
            print(f"Machine:     {plat['machine']}")
        uid = self._mpy.unique_id()
        if uid:
            print(f"Serial:      {uid}")
        for iface, mac in self._mpy.mac_addresses():
            print(f"MAC {iface + ':':<8} {mac}")
        mem = self._mpy.memory()
        mem_pct = (mem['alloc'] / mem['total'] * 100) if mem['total'] > 0 else 0
        print(f"Memory:      {self.format_size(mem['alloc'])} / {self.format_size(mem['total'])} ({mem_pct:.2f}%)")
        for fs in self._mpy.filesystems():
            label = "Flash:" if fs['mount'] == '/' else fs['mount'] + ':'
            fs_pct = (fs['used'] / fs['total'] * 100) if fs['total'] > 0 else 0
            print(f"{label:12} {self.format_size(fs['used'])} / {self.format_size(fs['total'])} ({fs_pct:.2f}%)")

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
            self._mpy.soft_reset()
        elif mode == 'raw':
            self._mpy.soft_reset_raw()
        elif mode == 'machine':
            if reconnect:
                try:
                    self.verbose("  reconnecting...", 1, color='yellow')
                    self._mpy.machine_reset(
                        reconnect=True, timeout=timeout)
                    self.verbose("  connected", 1, color='green')
                except (_mpytool.ConnError, OSError) as err:
                    self.verbose(
                        f"  reconnect failed: {err}", 1, color='red')
                    raise _mpytool.ConnError(
                        f"Reconnect failed: {err}")
            else:
                self._mpy.machine_reset(reconnect=False)
        elif mode == 'rts':
            try:
                self._mpy.hard_reset()
                if reconnect:
                    self.verbose(
                        "  reconnecting...", 1, color='yellow')
                    _time.sleep(1.0)  # Wait for device to boot
                    self._mpy._conn.reconnect()
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
            self._mpy.machine_bootloader()
        elif mode == 'dtr-boot':
            try:
                self._mpy.reset_to_bootloader()
            except NotImplementedError:
                raise _mpytool.MpyError(
                    "DTR boot not available (serial only)")

    # -- dispatch methods for process_commands() --

    def _dispatch_ls(self, commands, is_last_group):
        dir_name = ':'
        if commands:
            dir_name = commands.pop(0)
            # Strip trailing / except for root
            if dir_name not in (':', ':/'):
                dir_name = dir_name.rstrip('/')
        path = _parse_device_path(dir_name, 'ls')
        result = self._mpy.ls(path)
        for name, size in result:
            if size is not None:
                print(f'{self.format_size(size):>9} {name}')
            else:
                print(f'{"":9} {name}/')

    def _dispatch_tree(self, commands, is_last_group):
        dir_name = ':'
        if commands:
            dir_name = commands.pop(0)
            if dir_name not in (':', ':/'):
                dir_name = dir_name.rstrip('/')
        path = _parse_device_path(dir_name, 'tree')
        tree = self._mpy.tree(path)
        self.print_tree(tree)

    def _dispatch_cat(self, commands, is_last_group):
        if not commands:
            raise ParamsError('missing file name for cat command')
        for file_name in commands:
            path = _parse_device_path(file_name, 'cat')
            self.verbose(f"CAT: {path}", 2)
            data = self._mpy.get(path)
            print(data.decode('utf-8'))
        commands.clear()

    def _dispatch_mkdir(self, commands, is_last_group):
        if not commands:
            raise ParamsError('missing directory name for mkdir command')
        for dir_name in commands:
            path = _parse_device_path(dir_name, 'mkdir')
            self.verbose(f"MKDIR: {path}", 1)
            self._mpy.mkdir(path)
        commands.clear()

    def _dispatch_rm(self, commands, is_last_group):
        if not commands:
            raise ParamsError('missing file name for rm command')
        self.cmd_rm(*commands)
        commands.clear()

    def _dispatch_pwd(self, commands, is_last_group):
        cwd = self._mpy.getcwd()
        print(cwd)

    def _dispatch_cd(self, commands, is_last_group):
        if not commands:
            raise ParamsError('missing directory for cd command')
        dir_name = commands.pop(0)
        path = _parse_device_path(dir_name, 'cd')
        self.verbose(f"CD: {path}", 2)
        self._mpy.chdir(path)

    def _dispatch_reset(self, commands, is_last_group):
        mode = 'soft'
        timeout = None
        _reset_modes = (
            '--machine', '--rts', '--raw',
            '--boot', '--dtr-boot')
        while commands and commands[0].startswith('-'):
            flag = commands[0]
            if flag in _reset_modes:
                mode = commands.pop(0)[2:]  # strip --
            elif flag in ('-t', '--timeout'):
                commands.pop(0)
                if commands:
                    timeout = int(commands.pop(0))
                else:
                    raise ParamsError(
                        'missing value for --timeout')
            else:
                raise ParamsError(
                    f"unknown reset flag: '{flag}'")
        if timeout and mode not in ('machine', 'rts'):
            raise ParamsError(
                '--timeout only with --machine or --rts')
        has_more = bool(commands) or not is_last_group
        reconnect = has_more if mode in (
            'machine', 'rts') else True
        self.cmd_reset(
            mode=mode, reconnect=reconnect,
            timeout=timeout)

    def _dispatch_monitor(self, commands, is_last_group):
        self.cmd_monitor()
        commands.clear()

    def _dispatch_repl(self, commands, is_last_group):
        self.cmd_repl()
        commands.clear()

    def _dispatch_exec(self, commands, is_last_group):
        if not commands:
            raise ParamsError('missing code for exec command')
        code = commands.pop(0)
        self.verbose(f"EXEC: {code}", 1)
        result = self._mpy.comm.exec(code)
        if result:
            print(result.decode('utf-8', 'backslashreplace'), end='')

    def _dispatch_info(self, commands, is_last_group):
        self.cmd_info()

    def _dispatch_flash(self, commands, is_last_group):
        if commands and commands[0] == 'read':
            commands.pop(0)
            if len(commands) >= 2:
                # ESP32: flash read <label> <file>
                label = commands.pop(0)
                dest_path = commands.pop(0)
                self.cmd_flash_read(dest_path, label=label)
            elif len(commands) == 1:
                # RP2: flash read <file>
                dest_path = commands.pop(0)
                self.cmd_flash_read(dest_path)
            else:
                raise ParamsError('flash read requires destination file')
        elif commands and commands[0] == 'write':
            commands.pop(0)
            if len(commands) >= 2:
                # ESP32: flash write <label> <file>
                label = commands.pop(0)
                src_path = commands.pop(0)
                self.cmd_flash_write(src_path, label=label)
            elif len(commands) == 1:
                # RP2: flash write <file>
                src_path = commands.pop(0)
                self.cmd_flash_write(src_path)
            else:
                raise ParamsError('flash write requires source file')
        elif commands and commands[0] == 'erase':
            commands.pop(0)
            full = False
            label = None
            while commands and (commands[0] == '--full' or not commands[0].startswith('-')):
                if commands[0] == '--full':
                    full = True
                    commands.pop(0)
                elif label is None:
                    label = commands.pop(0)
                else:
                    break
            self.cmd_flash_erase(label=label, full=full)
        else:
            self.cmd_flash()

    def _dispatch_ota(self, commands, is_last_group):
        if not commands:
            raise ParamsError('ota requires firmware file path')
        self.cmd_ota(commands.pop(0))

    def _dispatch_sleep(self, commands, is_last_group):
        if not commands:
            raise ParamsError('sleep requires a number (seconds)')
        try:
            seconds = float(commands.pop(0))
        except ValueError:
            raise ParamsError('sleep requires a number (seconds)')
        self.verbose(f"SLEEP {seconds}s", 1)
        _time.sleep(seconds)

    def _dispatch_cp(self, commands, is_last_group):
        if len(commands) < 2:
            raise ParamsError('cp requires source and destination')
        self.cmd_cp(*commands)
        commands.clear()

    def _dispatch_mv(self, commands, is_last_group):
        if len(commands) < 2:
            raise ParamsError('mv requires source and destination')
        self.cmd_mv(*commands)
        commands.clear()

    def _dispatch_paths(self, commands, is_last_group):
        # For shell completion
        dir_name = commands.pop(0) if commands else ':'
        path = _parse_device_path(dir_name, '_paths')
        try:
            entries = self._mpy.ls(path)
        except (_mpytool.DirNotFound, _mpytool.MpyError):
            return
        for name, size in entries:
            print(name + '/' if size is None else name)

    # -- command dispatching --

    _COMMANDS = frozenset({
        'ls', 'tree', 'cat', 'mkdir', 'rm', 'pwd', 'cd',
        'reset', 'monitor', 'repl', 'exec', 'info', 'flash',
        'ota', 'sleep', 'cp', 'mv', '_paths',
    })

    def process_commands(self, commands, is_last_group=False):
        while commands:
            command = commands.pop(0)
            if command not in self._COMMANDS:
                raise ParamsError(f"unknown command: '{command}'")
            dispatch = getattr(self, f'_dispatch_{command.lstrip("_")}')
            dispatch(commands, is_last_group)
        try:
            self._mpy.comm.exit_raw_repl()
        except _mpytool.ConnError:
            pass  # connection already lost


if _about:
    _VERSION_STR = "%s %s (%s)" % (_about["Name"], _about["Version"], _about["Author-email"])
else:
    _VERSION_STR = "mpytool (not installed version)"
_COMMANDS_HELP_STR = """
Commands (: prefix = device path, :/ = root, : = CWD):
  ls [:path]                    list files and sizes (default: CWD)
  tree [:path]                  list directory tree (default: CWD)
  cat {:path} [...]             print file content to stdout
  cp [-f] {src} [...] {dst}     copy files (-f = force overwrite)
  mv {:src} [...] {:dst}        move/rename on device
  mkdir {:path} [...]           create directory (with parents)
  rm {:path} [...]              delete file/dir (:path/ = contents only)
  pwd                           print current working directory
  cd {:path}                    change current working directory
  reset [flags]                  soft reset (Ctrl-D) by default
    --machine [-t {s}]            machine.reset() with reconnect
    --rts [-t {s}]                hardware reset via DTR/RTS signal
    --raw                         soft reset in raw REPL
    --boot                        enter bootloader (machine.bootloader)
    --dtr-boot                    bootloader via DTR/RTS (ESP32)
  monitor                       print device output (Ctrl+C to stop)
  repl                          interactive REPL [Unix only]
  exec {code}                   execute Python code
  info                          show device information
  flash                         show flash/partitions info
  flash read [{label}] {file}   read flash/partition to file
  flash write [{label}] {file}  write file to flash/partition
  flash erase [{label}] [--full]  erase flash/partition
  ota {firmware.app-bin}        OTA update (ESP32)
  sleep {seconds}               pause between commands
Use -- to chain commands:
  mpytool cp main.py : -- reset -- monitor
"""


def _run_commands(mpy_tool, command_groups, with_progress=True):
    """Execute command groups with optional batch progress tracking"""
    if not with_progress:
        for i, commands in enumerate(command_groups):
            is_last = (i == len(command_groups) - 1)
            mpy_tool.process_commands(commands, is_last_group=is_last)
        return
    i = 0
    while i < len(command_groups):
        is_copy, count, src_paths, dst_paths = mpy_tool.count_files_for_command(command_groups[i])
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
            is_copy_j, count_j, src_paths_j, dst_paths_j = mpy_tool.count_files_for_command(command_groups[j])
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
        valid_str = ', '.join(f'{v//1024}K' if v >= 1024 else str(v) for v in sorted(valid))
        raise _argparse.ArgumentTypeError(f"chunk size must be one of: {valid_str}")
    return num


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
        "-e", "--exclude", type=str, action='append', dest='exclude',
        help='exclude files/dirs (wildcards: *, ?), default: *.pyc, .*')
    parser.add_argument(
        '-f', '--force', action='store_true', help='force overwrite (skip unchanged check)')
    parser.add_argument(
        '-z', '--compress', action='store_true', default=None, help='force compression')
    parser.add_argument(
        '-Z', '--no-compress', action='store_true', help='disable compression')
    parser.add_argument(
        '-c', '--chunk-size', type=_parse_chunk_size, metavar='SIZE',
        help='transfer chunk size: 512, 1K, 2K, 4K, 8K, 16K, 32K (default: auto)')
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
            log.verbose(f"Using {port}", level=2)
        else:
            log.error("Multiple serial ports found. Use -p to specify one:")
            for p in ports:
                print(f"  {p}")
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
    # Determine compression setting: None=auto, True=force, False=disable
    compress = None
    if args.no_compress:
        compress = False
    elif args.compress:
        compress = True
    mpy_tool = MpyTool(
        conn, log=log, verbose=log, exclude_dirs=args.exclude,
        force=args.force, compress=compress, chunk_size=args.chunk_size)
    command_groups = _utils.split_commands(args.commands)
    try:
        _run_commands(mpy_tool, command_groups, with_progress=(args.verbose >= 1))
    except (_mpytool.MpyError, _mpytool.ConnError, _mpytool.Timeout) as err:
        log.error(err)
    except KeyboardInterrupt:
        # Clear partial progress line and show clean message
        log.verbose('Interrupted', level=0, overwrite=True)


if __name__ == '__main__':
    main()
