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
        if not d:c='/';continue
        c='/'+d if c=='/' else (c+'/'+d if c else d)
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
""",
        'partition_magic': """
def _mt_pmagic(label, size=512):
    parts = esp32.Partition.find(esp32.Partition.TYPE_DATA, label=label)
    if not parts:
        return None
    p = parts[0]
    buf = bytearray(size)
    p.readblocks(0, buf)
    # Return magic bytes and block size (ioctl 5)
    return bytes(buf), p.ioctl(5, 0)
""",
        'partition_find': """
def _mt_pfind(label):
    p = esp32.Partition.find(esp32.Partition.TYPE_APP, label=label)
    if not p:
        p = esp32.Partition.find(esp32.Partition.TYPE_DATA, label=label)
    return p[0] if p else None
"""}

    def __init__(self, conn, log=None, chunk_size=None):
        self._conn = conn
        self._log = log
        self._mpy_comm = _mpy_comm.MpyComm(conn, log=log)
        self._imported = []
        self._load_helpers = []
        self._chunk_size = chunk_size  # None = auto-detect
        self._platform = None  # Cached platform name

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
        self._platform = None
        Mpy._CHUNK_AUTO_DETECTED = None
        Mpy._DEFLATE_AVAILABLE = None

    def _get_platform(self):
        """Get cached platform name (e.g. 'esp32', 'rp2')"""
        if self._platform is None:
            self._platform = self.platform()['platform']
        return self._platform

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
        if self._chunk_size is not None:
            return self._chunk_size
        if Mpy._CHUNK_AUTO_DETECTED is not None:
            return Mpy._CHUNK_AUTO_DETECTED
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

        if compress is None:
            compress = self._detect_deflate()

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

    def platform(self):
        """Get platform information

        Returns:
            dict with keys:
                'platform': platform name (e.g. 'esp32')
                'version': MicroPython version string
                'impl': implementation name (e.g. 'micropython')
                'machine': machine description (or None)
        """
        self.import_module('sys')
        self.import_module('os')

        platform = self._mpy_comm.exec_eval("repr(sys.platform)")
        version = self._mpy_comm.exec_eval("repr(sys.version)")
        impl = self._mpy_comm.exec_eval("repr(sys.implementation.name)")

        try:
            uname = self._mpy_comm.exec_eval("tuple(os.uname())")
            machine = uname[4] if len(uname) > 4 else None
        except _mpy_comm.CmdError:
            machine = None

        return {
            'platform': platform,
            'version': version,
            'impl': impl,
            'machine': machine,
        }

    def memory(self):
        """Get memory (RAM) information

        Returns:
            dict with keys:
                'free': free RAM in bytes
                'alloc': allocated RAM in bytes
                'total': total RAM in bytes
        """
        self.import_module('gc')
        self._mpy_comm.exec("gc.collect()")

        mem_free = self._mpy_comm.exec_eval("gc.mem_free()")
        mem_alloc = self._mpy_comm.exec_eval("gc.mem_alloc()")

        return {
            'free': mem_free,
            'alloc': mem_alloc,
            'total': mem_free + mem_alloc,
        }

    def unique_id(self):
        """Get device unique ID (serial number)

        Returns:
            hex string or None if not available
        """
        try:
            return self._mpy_comm.exec_eval(
                "repr(__import__('machine').unique_id().hex())"
            )
        except _mpy_comm.CmdError:
            return None

    def mac_addresses(self):
        """Get network MAC addresses

        Returns:
            list of (interface_name, mac_address) tuples
        """
        addresses = []
        try:
            self.import_module('network')
            try:
                mac = self._mpy_comm.exec_eval(
                    "repr(network.WLAN(network.STA_IF).config('mac').hex(':'))"
                )
                addresses.append(('WiFi', mac))
            except _mpy_comm.CmdError:
                pass
            try:
                mac = self._mpy_comm.exec_eval(
                    "repr(network.WLAN(network.AP_IF).config('mac').hex(':'))"
                )
                if not addresses or mac != addresses[0][1]:
                    addresses.append(('WiFi AP', mac))
            except _mpy_comm.CmdError:
                pass
            try:
                mac = self._mpy_comm.exec_eval(
                    "repr(network.LAN().config('mac').hex(':'))"
                )
                addresses.append(('LAN', mac))
            except _mpy_comm.CmdError:
                pass
        except _mpy_comm.CmdError:
            pass
        return addresses

    def filesystems(self):
        """Get filesystem information

        Returns:
            list of dicts with keys: mount, total, free, used
        """
        self.import_module('os')
        result = []

        try:
            fs_stat = self._mpy_comm.exec_eval("os.statvfs('/')")
            fs_total = fs_stat[0] * fs_stat[2]
            fs_free = fs_stat[0] * fs_stat[3]
            if fs_total > 0:
                result.append({
                    'mount': '/',
                    'total': fs_total,
                    'free': fs_free,
                    'used': fs_total - fs_free,
                })
        except _mpy_comm.CmdError:
            pass

        # Check subdirectories for additional mount points
        try:
            root_dirs = self._mpy_comm.exec_eval(
                "[d[0] for d in os.ilistdir('/') if d[1] == 0x4000]"
            )
            for dirname in root_dirs:
                try:
                    path = '/' + dirname
                    sub_stat = self._mpy_comm.exec_eval(f"os.statvfs('{path}')")
                    sub_total = sub_stat[0] * sub_stat[2]
                    sub_free = sub_stat[0] * sub_stat[3]
                    # Skip if same as root or zero size
                    if sub_total == 0 or any(f['total'] == sub_total for f in result):
                        continue
                    result.append({
                        'mount': path,
                        'total': sub_total,
                        'free': sub_free,
                        'used': sub_total - sub_free,
                    })
                except _mpy_comm.CmdError:
                    pass
        except _mpy_comm.CmdError:
            pass

        return result

    def info(self):
        """Get all device information (convenience method)

        Returns:
            dict combining platform(), memory(), unique_id(),
            mac_addresses() and filesystems()
        """
        result = self.platform()
        result['unique_id'] = self.unique_id()
        result['mac_addresses'] = self.mac_addresses()
        result.update({f'mem_{k}': v for k, v in self.memory().items()})
        result['filesystems'] = self.filesystems()
        return result

    _PART_TYPES = {0: 'app', 1: 'data'}
    _PART_SUBTYPES = {
        # App subtypes (type 0)
        0: {0: 'factory', 16: 'ota_0', 17: 'ota_1', 18: 'ota_2', 19: 'ota_3', 32: 'test'},
        # Data subtypes (type 1)
        1: {0: 'ota', 1: 'phy', 2: 'nvs', 3: 'coredump', 4: 'nvs_keys',
            5: 'efuse', 128: 'esphttpd', 129: 'fat', 130: 'spiffs', 131: 'littlefs'},
    }
    # Subtypes that can contain a filesystem (for auto-detection)
    _FS_SUBTYPES = {129, 130, 131}  # fat, spiffs, littlefs

    def _detect_fs_from_magic(self, magic):
        """Detect filesystem type and details from magic bytes.

        Args:
            magic: First 512 bytes from partition/flash (boot sector)

        Returns:
            dict with keys:
                'type': filesystem type ('littlefs2', 'fat16', 'fat32', 'exfat', None)
                'block_size': block/cluster size in bytes (if detected)
                'label': volume label (if detected)
            or None if not enough data
        """
        if len(magic) < 16:
            return None

        result = {'type': None, 'block_size': None, 'label': None}

        # LittleFS v2: "littlefs" string at offset 8
        # Note: LittleFS uses inline metadata format, block_size is not at fixed offset
        # We detect the filesystem type but can't reliably get block_size from magic
        if magic[8:16] == b'littlefs':
            result['type'] = 'littlefs2'
            # Block size must be obtained from device (ioctl) or partition info
            return result

        # Check for FAT boot sector signature (need 512 bytes)
        if len(magic) >= 512:
            # Boot sector signature at 510-511
            if magic[510:512] == b'\x55\xAA':
                import struct
                # Bytes per sector (offset 11-12)
                bytes_per_sector = struct.unpack('<H', magic[11:13])[0]
                # Sectors per cluster (offset 13)
                sectors_per_cluster = magic[13]
                result['block_size'] = bytes_per_sector * sectors_per_cluster

                # Check for exFAT first (has "EXFAT   " at offset 3)
                if magic[3:11] == b'EXFAT   ':
                    result['type'] = 'exfat'
                    return result

                # FAT type string location differs between FAT16 and FAT32
                # FAT16: "FAT16   " at offset 54
                # FAT32: "FAT32   " at offset 82
                if magic[54:62] == b'FAT16   ':
                    result['type'] = 'fat16'
                    # Volume label at offset 43 (11 bytes)
                    label = magic[43:54].rstrip(b' \x00').decode('ascii', errors='ignore')
                    if label and label != 'NO NAME':
                        result['label'] = label
                elif magic[82:90] == b'FAT32   ':
                    result['type'] = 'fat32'
                    # Volume label at offset 71 (11 bytes)
                    label = magic[71:82].rstrip(b' \x00').decode('ascii', errors='ignore')
                    if label and label != 'NO NAME':
                        result['label'] = label
                elif magic[54:59] == b'FAT12':
                    result['type'] = 'fat12'
                else:
                    # Generic FAT (can't determine type)
                    result['type'] = 'fat'
                return result

        return result if result['type'] else None

    def _read_partition_magic(self, label, size=512):
        """Read first bytes from partition for filesystem detection.

        Args:
            label: Partition label
            size: Number of bytes to read

        Returns:
            tuple (magic_bytes, block_size) or None if read fails
        """
        try:
            self.load_helper('partition_magic')
            return self._mpy_comm.exec_eval(f"_mt_pmagic('{label}', {size})")
        except _mpy_comm.CmdError:
            return None

    def partitions(self):
        """Get ESP32 partition information

        Returns:
            dict with keys:
                'partitions': list of partition info dicts with keys:
                    label, type, type_name, subtype, subtype_name,
                    offset, size, encrypted, running
                'running': label of currently running partition
                'boot': label of boot partition
                'next_ota': label of next OTA partition (or None)
                'next_ota_size': size of next OTA partition (or None)

        Raises:
            MpyError: if not ESP32 or partition module not available
        """
        try:
            self.import_module('esp32')
        except _mpy_comm.CmdError:
            raise _mpy_comm.MpyError("Partition info not available (ESP32 only)")

        running = self._mpy_comm.exec_eval(
            "repr(esp32.Partition(esp32.Partition.RUNNING).info()[4])"
        )

        raw_parts = self._mpy_comm.exec_eval(
            "[p.info() for p in "
            "esp32.Partition.find(esp32.Partition.TYPE_APP) + "
            "esp32.Partition.find(esp32.Partition.TYPE_DATA)]"
        )

        partitions = []
        next_ota_size = None
        for ptype, subtype, offset, size, label, encrypted in raw_parts:
            type_name = self._PART_TYPES.get(ptype, str(ptype))
            subtype_name = self._PART_SUBTYPES.get(ptype, {}).get(subtype, str(subtype))
            part_info = {
                'label': label,
                'type': ptype,
                'type_name': type_name,
                'subtype': subtype,
                'subtype_name': subtype_name,
                'offset': offset,
                'size': size,
                'encrypted': encrypted,
                'running': label == running,
                'filesystem': None,
                'fs_block_size': None,
            }
            # Detect actual filesystem for data partitions with FS subtypes
            if ptype == 1 and subtype in self._FS_SUBTYPES:  # TYPE_DATA
                result = self._read_partition_magic(label)
                if result:
                    magic, block_size = result
                    part_info['fs_block_size'] = block_size
                    fs_info = self._detect_fs_from_magic(magic)
                    if fs_info:
                        part_info['filesystem'] = fs_info.get('type')
                        # For FAT, use cluster size from magic; for others use partition block size
                        if fs_info.get('block_size') and 'fat' in (fs_info.get('type') or ''):
                            part_info['fs_cluster_size'] = fs_info.get('block_size')
            partitions.append(part_info)

        try:
            boot = self._mpy_comm.exec_eval(
                "repr(esp32.Partition(esp32.Partition.BOOT).info()[4])"
            )
        except _mpy_comm.CmdError:
            boot = None

        # Get next OTA partition (get size and label separately to handle string eval)
        try:
            next_ota_size = self._mpy_comm.exec_eval(
                "esp32.Partition(esp32.Partition.RUNNING).get_next_update().info()[3]"
            )
            next_ota = self._mpy_comm.exec_eval(
                "repr(esp32.Partition(esp32.Partition.RUNNING).get_next_update().info()[4])"
            )
        except _mpy_comm.CmdError:
            next_ota = None
            next_ota_size = None

        return {
            'partitions': partitions,
            'running': running,
            'boot': boot,
            'next_ota': next_ota,
            'next_ota_size': next_ota_size,
        }

    def flash_info(self):
        """Get RP2 flash information

        Returns:
            dict with keys:
                'size': total flash size in bytes
                'block_size': block size in bytes
                'block_count': number of blocks
                'filesystem': detected filesystem type ('littlefs2', 'fat', 'unknown')

        Raises:
            MpyError: if not RP2 or rp2.Flash not available
        """
        try:
            self.import_module('rp2')
        except _mpy_comm.CmdError as err:
            raise _mpy_comm.MpyError("Flash info not available (RP2 only)") from err

        # Get flash info via ioctl
        # ioctl(4) = block count, ioctl(5) = block size
        self._mpy_comm.exec("_f = rp2.Flash()")
        info = self._mpy_comm.exec_eval("(_f.ioctl(4, 0), _f.ioctl(5, 0))")
        block_count, block_size = info
        size = block_count * block_size

        # Read first 512 bytes for filesystem detection
        self._mpy_comm.exec("_b = bytearray(512); _f.readblocks(0, _b)")
        magic = self._mpy_comm.exec_eval("bytes(_b)")

        # Use common filesystem detection
        fs_info = self._detect_fs_from_magic(magic)
        fs_type = fs_info.get('type') if fs_info else None
        fs_block_size = fs_info.get('block_size') if fs_info else None

        return {
            'size': size,
            'block_size': block_size,
            'block_count': block_count,
            'filesystem': fs_type or 'unknown',
            'fs_block_size': fs_block_size,
            'magic': magic[:16],
        }

    def flash_read(self, label=None, progress_callback=None):
        """Read flash/partition content

        Arguments:
            label: partition label (ESP32) or None (RP2 entire user flash)
            progress_callback: optional callback(transferred, total)

        Returns:
            bytes with flash/partition content

        Raises:
            MpyError: if wrong platform or partition not found
        """
        platform = self._get_platform()
        self.import_module('ubinascii as ub')

        if label:
            # ESP32 partition
            if platform != 'esp32':
                raise _mpy_comm.MpyError("Partition label requires ESP32")
            self.import_module('esp32')
            self.load_helper('partition_find')
            try:
                part_info = self._mpy_comm.exec_eval(f"_mt_pfind('{label}').info()")
            except _mpy_comm.CmdError:
                raise _mpy_comm.MpyError(f"Partition '{label}' not found")
            _, _, _, total_size, _, _ = part_info
            block_size = 4096
            self._mpy_comm.exec(f"_dev = _mt_pfind('{label}')")
        else:
            # RP2 flash
            if platform != 'rp2':
                raise _mpy_comm.MpyError("Flash read without label requires RP2")
            self.import_module('rp2')
            self._mpy_comm.exec("_dev = rp2.Flash()")
            info = self._mpy_comm.exec_eval("(_dev.ioctl(4, 0), _dev.ioctl(5, 0))")
            block_count, block_size = info
            total_size = block_count * block_size

        total_blocks = (total_size + block_size - 1) // block_size
        chunk_blocks = 8  # 32KB per iteration
        data = bytearray()
        block_num = 0

        while block_num < total_blocks:
            blocks_to_read = min(chunk_blocks, total_blocks - block_num)
            bytes_to_read = blocks_to_read * block_size
            self._mpy_comm.exec(
                f"_buf=bytearray({bytes_to_read}); _dev.readblocks({block_num}, _buf)")
            b64_data = self._mpy_comm.exec_eval("repr(ub.b2a_base64(_buf).decode())")
            chunk = base64.b64decode(b64_data)
            data.extend(chunk)
            block_num += blocks_to_read
            if progress_callback:
                progress_callback(min(block_num * block_size, total_size), total_size)

        self._mpy_comm.exec("del _dev")
        return bytes(data[:total_size])

    def flash_write(self, data, label=None, progress_callback=None, compress=None):
        """Write data to flash/partition

        WARNING: This will overwrite the filesystem! Use with caution.

        Arguments:
            data: bytes to write (will be padded to block size)
            label: partition label (ESP32) or None (RP2 entire user flash)
            progress_callback: optional callback(transferred, total) for RP2,
                              callback(transferred, total, wire_bytes) for ESP32
            compress: None=auto-detect, True=force, False=disable (ESP32 only)

        Returns:
            dict with keys: 'size', 'written', and for ESP32: 'wire_bytes', 'compressed'

        Raises:
            MpyError: if wrong platform, data too large, or partition not found
        """
        platform = self._get_platform()

        if label:
            # ESP32 partition - use _write_partition_data for compression support
            if platform != 'esp32':
                raise _mpy_comm.MpyError("Partition label requires ESP32")
            self.import_module('esp32')
            self.load_helper('partition_find')
            try:
                part_info = self._mpy_comm.exec_eval(f"_mt_pfind('{label}').info()")
            except _mpy_comm.CmdError:
                raise _mpy_comm.MpyError(f"Partition '{label}' not found")
            _, _, _, part_size, part_label, _ = part_info
            self._mpy_comm.exec(f"_dev = _mt_pfind('{label}')")

            wire_bytes, used_compress = self._write_partition_data(
                '_dev', data, len(data), part_size, progress_callback, compress)

            self._mpy_comm.exec("del _dev")
            self.import_module('gc')
            self._mpy_comm.exec("gc.collect()")

            return {
                'size': part_size,
                'written': len(data),
                'wire_bytes': wire_bytes,
                'compressed': used_compress,
            }
        else:
            # RP2 flash - simple block write
            if platform != 'rp2':
                raise _mpy_comm.MpyError("Flash write without label requires RP2")
            self.import_module('rp2')
            self._mpy_comm.exec("_dev = rp2.Flash()")
            info = self._mpy_comm.exec_eval("(_dev.ioctl(4, 0), _dev.ioctl(5, 0))")
            block_count, block_size = info
            total_size = block_count * block_size

            if len(data) > total_size:
                raise _mpy_comm.MpyError(
                    f"Data too large: {len(data)} bytes, flash size: {total_size} bytes")

            self.import_module('ubinascii as ub')

            # Pad data to block size
            if len(data) % block_size:
                padding = block_size - (len(data) % block_size)
                data = data + b'\xff' * padding

            chunk_blocks = 8  # 32KB per iteration
            block_num = 0
            total_blocks = len(data) // block_size

            while block_num < total_blocks:
                blocks_to_write = min(chunk_blocks, total_blocks - block_num)
                offset = block_num * block_size
                chunk = data[offset:offset + blocks_to_write * block_size]
                b64_chunk = base64.b64encode(chunk).decode('ascii')
                self._mpy_comm.exec(f"_buf=ub.a2b_base64('{b64_chunk}')")
                self._mpy_comm.exec(f"_dev.writeblocks({block_num}, _buf)")
                block_num += blocks_to_write
                if progress_callback:
                    progress_callback(block_num * block_size, len(data))

            self._mpy_comm.exec("del _dev")
            return {
                'size': total_size,
                'written': len(data),
            }

    def flash_erase(self, label=None, full=False, progress_callback=None):
        """Erase flash/partition

        Arguments:
            label: partition label (ESP32) or None (RP2 entire user flash)
            full: if True, erase entire flash/partition; if False, erase first 2 blocks
            progress_callback: optional callback(transferred, total)

        Returns:
            dict with keys: 'erased', and 'label' for ESP32

        Raises:
            MpyError: if wrong platform or partition not found
        """
        platform = self._get_platform()

        if label:
            # ESP32 partition
            if platform != 'esp32':
                raise _mpy_comm.MpyError("Partition label requires ESP32")
            self.import_module('esp32')
            self.load_helper('partition_find')
            try:
                part_info = self._mpy_comm.exec_eval(f"_mt_pfind('{label}').info()")
            except _mpy_comm.CmdError:
                raise _mpy_comm.MpyError(f"Partition '{label}' not found")
            _, _, _, part_size, _, _ = part_info
            block_size = 4096
            total_blocks = part_size // block_size
            self._mpy_comm.exec(f"_dev = _mt_pfind('{label}')")
        else:
            # RP2 flash
            if platform != 'rp2':
                raise _mpy_comm.MpyError("Flash erase without label requires RP2")
            self.import_module('rp2')
            self._mpy_comm.exec("_dev = rp2.Flash()")
            info = self._mpy_comm.exec_eval("(_dev.ioctl(4, 0), _dev.ioctl(5, 0))")
            total_blocks, block_size = info

        if full:
            blocks_to_erase = total_blocks
        else:
            blocks_to_erase = min(2, total_blocks)  # First 2 blocks for FS reset

        total_bytes = blocks_to_erase * block_size

        # Prepare empty block buffer on device
        self._mpy_comm.exec(f"_buf = b'\\xff' * {block_size}")

        for block_num in range(blocks_to_erase):
            self._mpy_comm.exec(f"_dev.writeblocks({block_num}, _buf)")
            if progress_callback:
                progress_callback((block_num + 1) * block_size, total_bytes)

        self._mpy_comm.exec("del _dev")

        result = {'erased': total_bytes}
        if label:
            result['label'] = label
        return result

    def soft_reset(self):
        """Soft reset device (Ctrl-D in REPL)

        Runs boot.py and main.py after reset.
        """
        self._mpy_comm.soft_reset()
        self.reset_state()

    def soft_reset_raw(self):
        """Soft reset in raw REPL mode

        Clears RAM but doesn't run boot.py/main.py.
        """
        self._mpy_comm.soft_reset_raw()
        self.reset_state()

    def machine_reset(self, reconnect=True, timeout=None):
        """MCU reset using machine.reset()

        Arguments:
            reconnect: if True, attempt to reconnect after reset
            timeout: reconnect timeout in seconds (None = default)

        Returns:
            True if reconnected successfully, False otherwise

        Note: For USB-CDC ports, the port may disappear and reappear.
        """
        self._mpy_comm.enter_raw_repl()
        self._conn.write(b"import machine; machine.reset()\x04")
        self.reset_state()
        if reconnect:
            self._conn.reconnect(timeout=timeout)
            return True
        return False

    def machine_bootloader(self):
        """Enter bootloader using machine.bootloader()

        Note: Connection will be lost after this call.
        """
        self._mpy_comm.enter_raw_repl()
        self._conn.write(b"import machine; machine.bootloader()\x04")
        self.reset_state()

    def hard_reset(self):
        """Hardware reset using RTS signal (serial only)

        Raises:
            NotImplementedError: if connection doesn't support hardware reset
        """
        self._conn.hard_reset()
        self.reset_state()

    def reset_to_bootloader(self):
        """Enter bootloader using DTR/RTS signals (ESP32 serial only)

        Raises:
            NotImplementedError: if connection doesn't support this
        """
        self._conn.reset_to_bootloader()
        self.reset_state()

    def _write_partition_data(
            self, part_var, data, data_size, part_size,
            progress_callback=None, compress=None):
        """Write data to partition (shared implementation)

        Arguments:
            part_var: variable name holding partition on device (e.g. '_part')
            data: bytes to write
            data_size: size of data
            part_size: partition size (for validation)
            progress_callback: optional callback(transferred, total, wire_bytes)
            compress: None=auto-detect, True=force, False=disable

        Returns:
            tuple: (wire_bytes, used_compress)
        """
        if data_size > part_size:
            raise _mpy_comm.MpyError(
                f"Data too large: {data_size} > {part_size} bytes"
            )

        if compress is None:
            compress = self._detect_deflate()

        flash_block = 4096
        chunk_size = self._detect_chunk_size()
        chunk_size = max(flash_block, (chunk_size // flash_block) * flash_block)

        self.import_module('ubinascii as ub')
        if compress:
            self.import_module('deflate as df')
            self.import_module('io as _io')

        block_num = 0
        offset = 0
        wire_bytes = 0
        used_compress = False

        while offset < data_size:
            chunk = data[offset:offset + chunk_size]
            chunk_len = len(chunk)

            # Pad last chunk to flash block size
            if chunk_len % flash_block:
                padding = flash_block - (chunk_len % flash_block)
                chunk = chunk + b'\xff' * padding

            if compress:
                compressed = zlib.compress(chunk)
                comp_b64 = base64.b64encode(compressed).decode('ascii')
                raw_b64 = base64.b64encode(chunk).decode('ascii')
                if len(comp_b64) < len(raw_b64) - 20:
                    cmd = f"{part_var}.writeblocks({block_num}, df.DeflateIO(_io.BytesIO(ub.a2b_base64('{comp_b64}'))).read())"
                    wire_bytes += len(comp_b64)
                    used_compress = True
                else:
                    cmd = f"{part_var}.writeblocks({block_num}, ub.a2b_base64('{raw_b64}'))"
                    wire_bytes += len(raw_b64)
            else:
                raw_b64 = base64.b64encode(chunk).decode('ascii')
                cmd = f"{part_var}.writeblocks({block_num}, ub.a2b_base64('{raw_b64}'))"
                wire_bytes += len(raw_b64)

            self._mpy_comm.exec(cmd, timeout=30)

            blocks_written = len(chunk) // flash_block
            block_num += blocks_written
            offset += chunk_size

            if progress_callback:
                progress_callback(min(offset, data_size), data_size, wire_bytes)

        return wire_bytes, used_compress

    def ota_write(self, data, progress_callback=None, compress=None):
        """Write firmware data to next OTA partition

        Arguments:
            data: bytes with firmware content (.app-bin)
            progress_callback: optional callback(transferred, total, wire_bytes)
            compress: None=auto-detect, True=force, False=disable

        Returns:
            dict with keys:
                'target': label of target partition
                'size': firmware size
                'wire_bytes': bytes sent over wire
                'compressed': whether compression was used

        Raises:
            MpyError: if OTA not available or firmware too large
        """
        try:
            self.import_module('esp32')
        except _mpy_comm.CmdError:
            raise _mpy_comm.MpyError("OTA not available (ESP32 only)")

        try:
            part_info = self._mpy_comm.exec_eval(
                "esp32.Partition(esp32.Partition.RUNNING).get_next_update().info()"
            )
        except _mpy_comm.CmdError:
            raise _mpy_comm.MpyError("OTA not available (no OTA partitions)")

        part_type, part_subtype, part_offset, part_size, part_label, _ = part_info
        fw_size = len(data)

        self._mpy_comm.exec("_part = esp32.Partition(esp32.Partition.RUNNING).get_next_update()")

        wire_bytes, used_compress = self._write_partition_data(
            '_part', data, fw_size, part_size, progress_callback, compress
        )

        self._mpy_comm.exec("_part.set_boot()")

        self._mpy_comm.exec("del _part")
        self.import_module('gc')
        self._mpy_comm.exec("gc.collect()")

        return {
            'target': part_label,
            'offset': part_offset,
            'size': fw_size,
            'wire_bytes': wire_bytes,
            'compressed': used_compress,
        }

