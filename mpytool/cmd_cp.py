"""Copy command handler"""

import hashlib as _hashlib
import os as _os
import time as _time

import mpytool as _mpytool
import mpytool.utils as _utils
from mpytool.mpy_cross import MpyCross


# Import ParamsError from mpytool to avoid duplicate class definition
def _get_params_error():
    from mpytool.mpytool import ParamsError
    return ParamsError


ParamsError = _get_params_error()


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


class CopyCommand:
    """File copy command handler"""

    # Encoding info strings for alignment
    _ENC_WIDTH = 22  # Length of longest: "  (base64, compressed)"

    def __init__(
            self, mpy, log, verbose_callback, is_tty_callback,
            verbose_level_callback, exclude_dirs=None, is_excluded_fn=None):
        """Initialize CopyCommand.

        Arguments:
            mpy: Mpy instance for device communication
            log: Logger instance
            verbose_callback: callable(msg, level, color, end, overwrite)
            is_tty_callback: callable() -> bool
            verbose_level_callback: callable() -> int (0=quiet, 1=default, 2=verbose)
            exclude_dirs: set of patterns to exclude
            is_excluded_fn: callable(name) -> bool for exclusion check
        """
        self.mpy = mpy
        self._log = log
        self._verbose_cb = verbose_callback
        self._is_tty_cb = is_tty_callback
        self._verbose_level_cb = verbose_level_callback
        self._is_excluded_fn = is_excluded_fn

        # Settings (per-command, set via run())
        self._force = False
        self._compress = None
        self._mpy_cross = None

        # Progress tracking
        self._progress_total_files = 0
        self._progress_current_file = 0
        self._progress_src = ''
        self._progress_dst = ''
        self._progress_max_src_len = 0
        self._progress_max_dst_len = 0

        # Stats
        self._batch_mode = False
        self._skipped_files = 0
        self._stats_total_bytes = 0
        self._stats_transferred_bytes = 0
        self._stats_wire_bytes = 0
        self._stats_transferred_files = 0
        self._stats_start_time = None

        # Cache
        self._remote_file_cache = {}

        # Debug mode
        self._is_debug = getattr(self._log, '_loglevel', 1) >= 4

    @property
    def _verbose(self):
        return self._verbose_level_cb()

    @property
    def _is_tty(self):
        return self._is_tty_cb()

    def verbose(self, msg, level=1, color='green', end='\n', overwrite=False):
        self._verbose_cb(msg, level, color, end, overwrite)

    def _is_excluded(self, name):
        """Check if name matches any exclude pattern"""
        if self._is_excluded_fn:
            return self._is_excluded_fn(name)
        return False

    # --- Formatting methods ---

    @staticmethod
    def _format_local_path(path):
        """Format local path: relative from CWD, absolute if > 2 levels up"""
        try:
            rel_path = _os.path.relpath(path)
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

    # --- Progress callbacks ---

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

    # --- File collection methods ---

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

    def _collect_destination_paths(self, sources, dest, dest_is_remote):
        """Collect formatted destination paths for cp command"""
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

    # --- Transfer operations ---

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

    # --- Public API ---

    def run(self, paths, force=False, compress=None, mpy=False):
        """Execute copy command.

        Arguments:
            paths: list of source(s) and destination
            force: overwrite without checking
            compress: True/False/None (auto-detect)
            mpy: compile .py to .mpy
        """
        if len(paths) < 2:
            raise ParamsError('cp requires source and destination')

        # Save and set flags for this command
        self._force = force
        self._compress = compress
        if mpy:
            self._mpy_cross = MpyCross(self._log, self.verbose)

        try:
            self._run_impl(paths)
        finally:
            self._mpy_cross = None

    def _run_impl(self, args):
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

        # Determine if destination is a directory
        if dest_is_remote:
            dest_is_dir = (
                dest_path == '' or dest_path == '/'
                or dest_path.endswith('/'))
        else:
            dest_is_dir = (
                dest_path.endswith('/') or _os.path.isdir(dest_path))

        # Check: contents (trailing slash) or multiple sources
        has_multi_source = len(sources) > 1
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

    def count_files(self, sources, dest):
        """Count files for batch progress.

        Returns:
            (file_count, source_paths, dest_paths)
        """
        src_paths = []
        for src in sources:
            src_is_remote = src.startswith(':')
            src_path = src[1:] if src_is_remote else src
            if not src_path:
                src_path = '/'
            src_path = src_path.rstrip('/') or '/'
            if src_is_remote:
                src_paths.extend(self._collect_remote_paths(src_path))
            else:
                src_paths.extend(self._collect_local_paths(src_path))
        dest_is_remote = dest.startswith(':')
        dst_paths = self._collect_destination_paths(sources, dest, dest_is_remote)
        return len(src_paths), src_paths, dst_paths

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

    def print_summary(self):
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
