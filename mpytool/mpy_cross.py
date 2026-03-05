"""Mpy-cross compilation support for MicroPython .mpy files"""

import fnmatch as _fnmatch
import os as _os
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import sys as _sys

from mpytool.logger import SimpleColorLogger as _SimpleColorLogger


# Native arch index from sys.implementation._mpy bits 10-13
_MPY_ARCH_NAMES = (
    None, 'x86', 'x64', 'armv6', 'armv6m', 'armv7m', 'armv7em',
    'armv7emsp', 'armv7emdp', 'xtensa', 'xtensawin', 'rv32imc',
    'rv64imc',
)

BOOT_FILES = frozenset(('boot.py', 'main.py'))


def _find_mpy_cross():
    """Find mpy-cross binary: first in same dir as Python, then in PATH"""
    # Look next to current Python interpreter (same venv)
    bin_dir = _os.path.dirname(_sys.executable)
    local = _os.path.join(bin_dir, 'mpy-cross')
    if _os.path.isfile(local) and _os.access(local, _os.X_OK):
        return local
    # Windows: mpy-cross.exe
    local_exe = local + '.exe'
    if _os.path.isfile(local_exe) and _os.access(local_exe, _os.X_OK):
        return local_exe
    # Fallback to PATH
    return _shutil.which('mpy-cross')


class MpyCross:
    """Mpy-cross compiler wrapper with caching

    Compiles .py files to .mpy bytecode via mpy-cross.
    Cached in __pycache__/name.mpy-X.Y-arch.mpy with mtime check.
    """

    def __init__(self, log=None):
        self._log = log if log is not None else _SimpleColorLogger()
        self.active = False
        self._bin = None  # path to mpy-cross binary
        self._ver = None  # (major, sub) device mpy version
        self._arch = None  # architecture name for cache key
        self._args = []  # extra mpy-cross args (-b, -march)
        self.compiled = {}  # {src_path: cache_path}

    @property
    def ver(self):
        """Device mpy version as (major, sub) tuple or None"""
        return self._ver

    @property
    def arch(self):
        """Device architecture name or None"""
        return self._arch

    def init(self, platform_info):
        """Initialize: find mpy-cross, check version, detect arch

        Arguments:
            platform_info: dict from Mpy.platform()

        Sets self.active = True on success, False on failure.
        """
        self.active = False
        mpy_cross_bin = _find_mpy_cross()
        if not mpy_cross_bin:
            self._log.warning(
                'mpy-cross not found in PATH (pip install mpy-cross),'
                ' uploading .py files')
            return
        # Get mpy-cross version
        result = _subprocess.run(
            [mpy_cross_bin, '--version'],
            capture_output=True, text=True, timeout=10)
        output = result.stdout + result.stderr
        match = _re.search(r'mpy v(\d+)\.(\d+)', output)
        if not match:
            self._log.warning(
                f'Cannot parse mpy-cross version: {output.strip()},'
                ' uploading .py files')
            return
        cross_ver = int(match.group(1))
        cross_sub = int(match.group(2))
        # Get device mpy version and architecture
        dev_ver = platform_info.get('mpy_ver')
        dev_sub = platform_info.get('mpy_sub')
        if dev_ver is None:
            self._log.warning(
                'Device does not report mpy version, uploading .py files')
            return
        self._ver = (dev_ver, dev_sub)
        self._args = ['-O2']
        # Bytecode version targeting
        cross_str = f'v{cross_ver}.{cross_sub}'
        if cross_ver != dev_ver or cross_sub != dev_sub:
            self._args += ['-b', f'{dev_ver}.{dev_sub}']
            cross_str += f' -> v{dev_ver}.{dev_sub}'
        # Native architecture for @native/@viper support
        dev_arch = platform_info.get('mpy_arch', 0)
        arch_name = None
        if dev_arch and dev_arch < len(_MPY_ARCH_NAMES):
            arch_name = _MPY_ARCH_NAMES[dev_arch]
        self._arch = arch_name
        if arch_name:
            self._args.append('-march=' + arch_name)
        dev_version = platform_info.get('version', '')
        arch_str = f' arch {arch_name}' if arch_name else ''
        self._log.debug(
            'device v%s mpy v%d.%d%s, mpy-cross %s',
            dev_version, dev_ver, dev_sub, arch_str, cross_str)
        self._bin = mpy_cross_bin
        self.active = True

    def compile(self, src_path):
        """Compile .py file to .mpy, return cache path or None

        Skips boot.py, main.py, and non-.py files.
        Uses cache if fresh (mtime check).
        Returns None if not active (init() not called or failed).
        """
        if not self.active:
            return None
        basename = _os.path.basename(src_path)
        if basename in BOOT_FILES:
            self._log.debug('mpy: skip %s (boot file)', basename)
            return None
        if not basename.endswith('.py'):
            return None
        stem = basename[:-3]
        ver, sub = self._ver
        arch_suffix = f'-{self._arch}' if self._arch else ''
        cache_dir = _os.path.join(_os.path.dirname(src_path), '__pycache__')
        cache_path = _os.path.join(
            cache_dir, f'{stem}.mpy-{ver}.{sub}{arch_suffix}.mpy')
        # Check if cache is fresh
        if _os.path.exists(cache_path):
            if _os.path.getmtime(cache_path) >= _os.path.getmtime(src_path):
                self.compiled[src_path] = cache_path
                return cache_path
        # Compile
        _os.makedirs(cache_dir, exist_ok=True)
        cmd = [self._bin] + self._args + ['-o', cache_path, src_path]
        self._log.info('$ %s', ' '.join(cmd))
        result = _subprocess.run(
            cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            self._log.warning(f'mpy-cross failed for {basename}: {err}')
            return None
        self.compiled[src_path] = cache_path
        return cache_path

    def find_compiled(self, py_path):
        """Find .mpy for given .py source (prebuilt or cached)

        Checks:
        1. Prebuilt .mpy in same directory as .py
        2. Cached .mpy from previous compilation

        Returns:
            Path to .mpy file, or None if not found
        """
        mpy_path = py_path[:-3] + '.mpy'
        if _os.path.exists(mpy_path):
            return mpy_path
        cache = self.compiled.get(py_path)
        if cache and _os.path.exists(cache):
            return cache
        return None

    def compile_sources(self, sources, is_excluded=None):
        """Pre-compile all .py source files to .mpy cache

        Arguments:
            sources: list of source paths (files or directories)
            is_excluded: optional callback(name) -> bool for exclusion check
        """
        for src in sources:
            if src.startswith(':'):
                continue
            src_path = src.rstrip('/')
            if _os.path.isfile(src_path):
                self.compile(src_path)
            elif _os.path.isdir(src_path):
                for root, dirs, files in _os.walk(src_path, topdown=True):
                    if is_excluded:
                        dirs[:] = sorted(
                            d for d in dirs if not is_excluded(d))
                    else:
                        dirs.sort()
                    for f in sorted(files):
                        if f.endswith('.py'):
                            self.compile(_os.path.join(root, f))
