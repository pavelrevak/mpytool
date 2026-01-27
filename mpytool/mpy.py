"""MicroPython tool: main MPY class"""

import base64
import zlib

import mpytool.mpy_comm as _mpy_comm


def _escape_path(path: str) -> str:
    """Escape path for use in Python string literal"""
    return path.replace("\\", "\\\\").replace("'", "\\'")


class PathNotFound(_mpy_comm.MpyError):
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


class Mpy():
    _CHUNK = 512
    _CHUNK_AUTO_DETECTED = None  # Will be set on first put() if not overridden
    _DEFLATE_AVAILABLE = None  # None = not checked, True/False = result
    _ATTR_DIR = 0x4000
    _ATTR_FILE = 0x8000
    # Helper functions for MicroPython device
    # Using _mt_ prefix (mpytool) to avoid collisions, short var names to minimize transfer
    _HELPERS = {
        'stat': f"""
def _mt_stat(p):
    try:
        s=os.stat(p)
        return -1 if s[0]=={_ATTR_DIR} else s[6] if s[0]=={_ATTR_FILE} else None
    except:return None
""",
        'tree': f"""
def _mt_tree(p):
    D,F,sz=[],[],0
    for e in os.ilistdir(p):
        n,a=e[:2]
        if a=={_ATTR_FILE}:
            F.append((n,e[3],None));sz+=e[3]
        elif a=={_ATTR_DIR}:
            _,s,t=_mt_tree((p+'/'if p not in('','/')else p)+n)
            D.append((n,s,t));sz+=s
    return p,sz,D+F
""",
        'mkdir': f"""
def _mt_mkdir(p):
    p=p.rstrip('/');c='';f=1
    for d in p.split('/'):
        c+='/'+d if c else d
        if f:
            try:
                if os.stat(c)[0]=={_ATTR_FILE}:return 1
                continue
            except:f=0
        os.mkdir(c)
    return 0
""",
        'rmdir': f"""
def _mt_rmdir(p):
    for n,a,_,_ in os.ilistdir(p):
        q=p+'/'+n
        if a=={_ATTR_FILE}:os.remove(q)
        elif a=={_ATTR_DIR}:_mt_rmdir(q)
    os.rmdir(p)
""",
        '_hash': """
def _mt_hash(p):
    h=hashlib.sha256()
    with open(p,'rb')as f:
        while 1:
            c=f.read(512)
            if not c:break
            h.update(c)
    return ubinascii.b2a_base64(h.digest()).strip()
""",
        'fileinfo': f"""
def _mt_finfo(files):
    r={{}}
    for p,xsz in files.items():
        try:
            s=os.stat(p)
            if s[0]!={_ATTR_FILE}:r[p]=None;continue
            sz=s[6]
            r[p]=(sz,None)if sz!=xsz else(sz,_mt_hash(p))
        except:r[p]=None
    gc.collect()
    return r
"""}

    def __init__(self, conn, log=None, chunk_size=None):
        self._conn = conn
        self._log = log
        self._mpy_comm = _mpy_comm.MpyComm(conn, log=log)
        self._imported = []
        self._load_helpers = []
        self._chunk_size = chunk_size  # None = auto-detect

    @property
    def conn(self):
        """access to connector instance
        """
        return self._conn

    @property
    def comm(self):
        """access to MPY communication instance
        """
        return self._mpy_comm

    def reset_state(self):
        """Reset internal state after device reset

        Call this after soft_reset() or hard_reset() to clear cached state.
        """
        self._imported = []
        self._load_helpers = []
        self._mpy_comm._repl_mode = None
        Mpy._CHUNK_AUTO_DETECTED = None
        Mpy._DEFLATE_AVAILABLE = None

    def load_helper(self, helper):
        """Load helper function to MicroPython

        Arguments:
            helper: helper function name
        """
        if helper not in self._load_helpers:
            if helper not in self._HELPERS:
                raise _mpy_comm.MpyError(f'Helper {helper} not defined')
            self._mpy_comm.exec(self._HELPERS[helper])
            self._load_helpers.append(helper)

    def import_module(self, module):
        """Import module to MicroPython

        Arguments:
            module: module name to import
        """
        if module not in self._imported:
            self._mpy_comm.exec(f'import {module}')
            self._imported.append(module)

    def stat(self, path):
        """Stat path

        Arguments:
            path: path to stat

        Returns:
            None: if path not exists
            -1: on folder
            >= 0: on file and it's size
        """
        self.import_module('os')
        self.load_helper('stat')
        return self._mpy_comm.exec_eval(f"_mt_stat('{_escape_path(path)}')")

    def ls(self, path=None):
        """List files on path

        Arguments:
            path: path to list, default: current path

        Returns:
            list of tuples, where each tuple for file:
                ('file_name', size)
            for directory:
                ('dir_name', None)
        """
        self.import_module('os')
        if path is None:
            path = ''
        try:
            result = self._mpy_comm.exec_eval(
                f"tuple(os.ilistdir('{_escape_path(path)}'))")
            res_dir = []
            res_file = []
            for entry in result:
                name, attr = entry[:2]
                if attr == self._ATTR_DIR:
                    res_dir.append((name, None))
                elif attr == self._ATTR_FILE:
                    size = entry[3]
                    res_file.append((name, size))
        except _mpy_comm.CmdError as err:
            raise DirNotFound(path) from err
        return res_dir + res_file

    def tree(self, path=None):
        """Tree of directory structure with sizes

        Arguments:
            path: path to list, default: current path

        Returns: entry of directory or file
            for directory:
                (dir_path, size, [list of sub-entries])
            for empty directory:
                (dir_path, size, [])
            for file:
                (file_path, size, None)
        """
        self.import_module('os')
        self.load_helper('tree')
        if path is None:
            path = ''
        if path in ('', '.', '/'):
            return self._mpy_comm.exec_eval(f"_mt_tree('{_escape_path(path)}')")
        # check if path exists
        result = self.stat(path)
        if result is None:
            raise DirNotFound(path)
        if result == -1:
            return self._mpy_comm.exec_eval(f"_mt_tree('{_escape_path(path)}')")
        return (path, result, None)

    def mkdir(self, path):
        """make directory (also create all parents)

        Arguments:
            path: new directory path
        """
        self.import_module('os')
        self.load_helper('mkdir')
        if self._mpy_comm.exec_eval(f"_mt_mkdir('{_escape_path(path)}')"):
            raise _mpy_comm.MpyError(f'Error creating directory, this is file: {path}')

    def delete(self, path):
        """delete file or directory (recursively)

        Arguments:
            path: path to delete
        """
        result = self.stat(path)
        if result is None:
            raise PathNotFound(path)
        if result == -1:
            self.import_module('os')
            self.load_helper('rmdir')
            self._mpy_comm.exec(f"_mt_rmdir('{_escape_path(path)}')", 20)
        else:
            self._mpy_comm.exec(f"os.remove('{_escape_path(path)}')")

    def rename(self, src, dst):
        """Rename/move file or directory

        Arguments:
            src: source path
            dst: destination path
        """
        self.import_module('os')
        self._mpy_comm.exec(f"os.rename('{_escape_path(src)}', '{_escape_path(dst)}')")

    def hashfile(self, path):
        """Compute SHA256 hash of file

        Arguments:
            path: file path

        Returns:
            bytes with SHA256 hash (32 bytes) or None if hashlib not available
        """
        self.import_module('hashlib')
        self.import_module('ubinascii')
        self.load_helper('_hash')
        try:
            result = self._mpy_comm.exec_eval(f"_mt_hash('{_escape_path(path)}')")
            return base64.b64decode(result) if result else None
        except _mpy_comm.CmdError:
            return None

    def fileinfo(self, files):
        """Get file info (size and hash) for multiple files in one call

        Arguments:
            files: dict {path: expected_size} - hash is only computed if sizes match

        Returns:
            dict {path: (size, hash)} - hash is None if sizes don't match
            dict {path: None} - if file doesn't exist
            Returns None if hashlib not available on device
        """
        self.import_module('os')
        self.import_module('gc')
        self.import_module('hashlib')
        self.import_module('ubinascii')
        self.load_helper('_hash')
        self.load_helper('fileinfo')
        escaped_files = {_escape_path(p): s for p, s in files.items()}
        # Timeout scales with number of files (base 5s + 0.5s per file)
        timeout = 5 + len(files) * 0.5
        try:
            result = self._mpy_comm.exec_eval(f"_mt_finfo({escaped_files})", timeout=timeout)
            # Decode base64 hashes
            for path, info in result.items():
                if info and info[1]:
                    result[path] = (info[0], base64.b64decode(info[1]))
            return result
        except _mpy_comm.CmdError:
            return None

    def get(self, path, progress_callback=None):
        """Read file

        Arguments:
            path: file path to read
            progress_callback: optional callback(transferred, total) for progress

        Returns:
            bytes with file content
        """
        # Get file size first if callback provided
        total_size = 0
        if progress_callback:
            total_size = self.stat(path)
            if total_size is None or total_size < 0:
                total_size = 0
        try:
            self._mpy_comm.exec(f"f = open('{_escape_path(path)}', 'rb')")
        except _mpy_comm.CmdError as err:
            raise FileNotFound(path) from err
        data = b''
        while True:
            result = self._mpy_comm.exec_eval(f"f.read({self._CHUNK})")
            if not result:
                break
            data += result
            if progress_callback:
                progress_callback(len(data), total_size)
        self._mpy_comm.exec("f.close()")
        return data

    def _encode_chunk(self, chunk, compress=False):
        """Encode chunk for transfer - choose smallest representation

        Arguments:
            chunk: bytes to encode
            compress: whether to try compression

        Returns:
            tuple (command_string, original_chunk_size, encoding_type)
            encoding_type is 'raw', 'base64', or 'compressed'
        """
        chunk_size = len(chunk)
        raw = repr(chunk)
        raw_len = len(raw)

        b64 = base64.b64encode(chunk).decode('ascii')
        b64_cmd = f"ub.a2b_base64('{b64}')"
        b64_len = len(b64_cmd)

        best_cmd = raw
        best_len = raw_len
        best_type = 'raw'

        if b64_len < best_len:
            best_cmd = b64_cmd
            best_len = b64_len
            best_type = 'base64'

        if compress:
            compressed = zlib.compress(chunk)
            comp_b64 = base64.b64encode(compressed).decode('ascii')
            comp_cmd = f"df.DeflateIO(_io.BytesIO(ub.a2b_base64('{comp_b64}'))).read()"
            comp_len = len(comp_cmd)
            if comp_len < best_len:
                best_cmd = comp_cmd
                best_len = comp_len
                best_type = 'compressed'

        return best_cmd, chunk_size, best_type

    def _detect_chunk_size(self):
        """Detect optimal chunk size based on device free RAM

        Returns:
            chunk size in bytes (512, 1024, 2048, 4096, 8192, 16384, or 32768)
        """
        # Return user-specified chunk size if provided
        if self._chunk_size is not None:
            return self._chunk_size
        if Mpy._CHUNK_AUTO_DETECTED is not None:
            return Mpy._CHUNK_AUTO_DETECTED
        # Get free RAM after garbage collection
        self.import_module('gc')
        self._mpy_comm.exec("gc.collect()")
        try:
            free = self._mpy_comm.exec_eval("gc.mem_free()")
        except _mpy_comm.CmdError:
            free = 0
        # Select chunk size based on free RAM (~10-15% of free RAM)
        if free > 256 * 1024:
            chunk = 32768
        elif free > 128 * 1024:
            chunk = 16384
        elif free > 64 * 1024:
            chunk = 8192
        elif free > 48 * 1024:
            chunk = 4096
        elif free > 32 * 1024:
            chunk = 2048
        elif free > 24 * 1024:
            chunk = 1024
        else:
            chunk = 512
        Mpy._CHUNK_AUTO_DETECTED = chunk
        return chunk

    def _detect_deflate(self):
        """Detect if deflate module is available and device has enough RAM

        Returns:
            True if deflate is available and RAM >= 64KB, False otherwise
        """
        if Mpy._DEFLATE_AVAILABLE is None:
            # Check RAM first - need at least 64KB for decompression
            chunk = self._detect_chunk_size()
            if chunk < 8192:  # chunk < 8K means RAM <= 64KB
                Mpy._DEFLATE_AVAILABLE = False
            else:
                try:
                    self._mpy_comm.exec("import deflate")
                    Mpy._DEFLATE_AVAILABLE = True
                except _mpy_comm.CmdError:
                    Mpy._DEFLATE_AVAILABLE = False
        return Mpy._DEFLATE_AVAILABLE

    def put(self, data, path, progress_callback=None, compress=None):
        """Write file to device

        Arguments:
            data: bytes with file content
            path: file path to write
            progress_callback: optional callback(transferred, total) for progress
            compress: None=auto-detect, True=force compression, False=disable

        Returns:
            tuple (encodings_used, wire_bytes) where:
                encodings_used: set of encoding types ('raw', 'base64', 'compressed')
                wire_bytes: number of bytes sent over the wire (encoded size)
        """
        chunk_size = self._detect_chunk_size()
        total_size = len(data)
        transferred = 0
        wire_bytes = 0
        encodings_used = set()

        # Resolve compression setting
        if compress is None:
            compress = self._detect_deflate()

        # Import modules for encoding
        self.import_module('ubinascii as ub')
        if compress:
            self.import_module('deflate as df')
            self.import_module('io as _io')

        self._mpy_comm.exec(f"f = open('{_escape_path(path)}', 'wb')")
        while data:
            chunk = data[:chunk_size]
            cmd, orig_size, enc_type = self._encode_chunk(chunk, compress)
            encodings_used.add(enc_type)
            # Wire bytes = command overhead (9 = "f.write(" + ")") + encoded data
            wire_bytes += 9 + len(cmd)
            count = self._mpy_comm.exec_eval(f"f.write({cmd})", timeout=10)
            data = data[orig_size:]
            transferred += orig_size
            if progress_callback:
                progress_callback(transferred, total_size)
        self._mpy_comm.exec("f.close()")
        # Run garbage collection to free memory and allow flash to settle
        self.import_module('gc')
        self._mpy_comm.exec("gc.collect()")
        return encodings_used, wire_bytes
