"""MicroPython tool: mount local directory on device as VFS

## Architecture

Three-layer architecture:
1. **Agent (MicroPython)**: VFS driver on device (~4.3KB)
   - RemoteFS class implements VFS interface
     (mount, stat, ilistdir, open, chdir, mkdir, remove, rename)
   - _mt_F class implements file object
     (read, write, readline, seek, close, context manager)
   - Sends VFS requests via escape sequences on serial line

2. **Handler (PC)**: Handles VFS requests from device
   (MountHandler class)
   - Resolves paths within mount root (prevents path traversal)
   - Services 11 VFS commands:
     STAT, LISTDIR, OPEN, CLOSE, READ, WRITE, MKDIR, REMOVE, RENAME, SEEK,
     READLINE
   - Manages file descriptors and open file handles
   - Supports virtual submounts (ln command)

3. **Protocol (PC)**: VFS protocol handler attached to connection
   (VfsProtocol class, registered via conn.register_escape_handler)
   - Intercepts escape sequences (0x18) and dispatches to handler
   - Passes through all other data (REPL I/O)
   - Detects soft reboot and triggers remount
   - Unknown MIDs use dismiss handler returning ENOENT

## Communication Protocol

Escape-based protocol runs transparently alongside REPL I/O:

**Escape byte:** 0x18 (CAN/Ctrl+X)
- Chosen because not used in raw REPL protocol

**Protocol flow:**
1. Device → PC: `\x18 CMD MID`
   (escape + command byte + mount ID byte)
2. Device blocks: waits for ACK
   (`while _mt_si.read(1)[0]!=_mt_E:pass`)
3. PC → Device: ACK `\x18` (unblocks device)
4. Device → PC: Parameters (path, mode, fd, data, ...)
   - Format depends on command
5. PC → Device: Response (error code or data)
   - Format depends on command
6. Device: Processes response, returns to Python code

**Why synchronous?**
Device waits for PC response before continuing. This ensures:
- Deterministic flow (no race conditions)
- Error propagation (PC can signal errors to device)
- Flow control (PC controls data transfer pace)

## VFS Commands

| CMD | Name           | Device → PC Params    | PC → Device Response |
|-----|----------------|-----------------------|----------------------|
| 1   | STAT           | path (str)            | errno, mode, size,   |
|     |                |                       | mtime                |
| 2   | LISTDIR        | path (str)            | errno (s8), count    |
|     |                |                       | (s32), entries:      |
|     |                |                       | [(name, mode, size)] |
| 4   | OPEN           | path, mode (str)      | fd (s8)              |
| 5   | CLOSE          | fd (s8)               | errno (s8)           |
| 6   | READ           | fd, count (s32)       | length, data         |
| 7   | WRITE          | fd, length, data      | errno (s8)           |
| 8   | MKDIR          | path (str)            | errno (s8)           |
| 9   | REMOVE         | path, recursive (s8)  | errno (s8)           |
| 10  | RENAME         | old_path, new_path    | errno (s8)           |
|     |                | (str)                 |                      |
| 11  | SEEK           | fd (s8), offset (s32),| position (s32)       |
|     |                | whence (s8)           |                      |
| 12  | READLINE       | fd (s8)               | length, data         |

**Wire format:**
- `s8`: signed 8-bit int (struct 'b')
- `s32`: signed 32-bit int little-endian (struct '<i')
- `u32`: unsigned 32-bit int little-endian (struct '<I')
- `str`: length (s32) + utf-8 bytes
- `bytes`: length (s32) + raw bytes

**Error codes:** Negative errno values
(e.g., -2 = ENOENT, -13 = EACCES, -30 = EROFS)

## Example Session

```
# Device executes: open('/remote/test.txt').read()

1. Device sends: \x18 \x01 \x00        # CMD_STAT (1), mid=0
2. Device blocks waiting for ACK
3. PC sends: \x18                      # ACK
4. Device sends: "\x0e\x00\x00\x00/remote/test.txt"
   # path length + path
5. PC responds: \x00 \x00\x80\x00\x00 \x0c\x00\x00\x00 ...
   # OK, S_IFREG, size=12
6. Device unblocks, proceeds to OPEN

7. Device sends: \x18 \x04 \x00        # CMD_OPEN (4), mid=0
8. PC sends: \x18                      # ACK
9. Device sends: path + mode 'r'
10. PC responds: \x00                  # fd=0
11. Device unblocks, creates _mt_F(fd=0)

12. Device sends: \x18 \x06 \x00       # CMD_READ (6), mid=0
13. PC sends: \x18                     # ACK
14. Device sends: \x00 \xff\xff\xff\xff  # fd=0, count=-1 (read all)
15. PC responds: \x0c\x00\x00\x00 + data  # length=12, data bytes
16. Device reads data, unblocks

17. Device sends: \x18 \x05 \x00       # CMD_CLOSE (5), mid=0
18. PC sends: \x18                     # ACK
19. Device sends: \x00                 # fd=0
20. PC responds: \x00                  # errno=0 (OK)
21. Device unblocks, fd closed
```

## Soft Reboot Handling

When device soft resets (Ctrl+D):
1. VfsProtocol detects "soft reboot" marker in output stream
2. Sets `_needs_remount = True` flag
3. Waits for REPL prompt ">>> "
4. Calls `remount_fn()` callback
5. `_do_remount_all()` re-injects agent, re-mounts all VFS,
   restores CWD
6. User can continue working seamlessly

## Security

**Path traversal protection:**
- All paths resolved via `os.path.realpath()`
- Validated that resolved path starts with mount root
- Symlinks are followed and validated
- Invalid paths return EACCES error

**Write protection:**
- Default: readonly mount (writable=False)
- Write operations return EROFS when readonly
- Requires explicit `-w` flag to enable writes

## Performance

**Chunk size:** Auto-detected 512B - 32KB based on device RAM
**Overhead:** ~1 RTT per VFS operation (blocking protocol)
**Optimal for:** Development, testing, prototyping
**Not optimal for:** High-throughput file I/O
(use cp/get/put for bulk transfers)
"""

import errno as _errno
import os as _os
import shutil as _shutil
import struct as _struct

from mpytool.mpy_comm import MpyError
from mpytool.mpy_cross import BOOT_FILES

# Protocol constants
ESCAPE = 0x18  # CAN / Ctrl+X
CMD_STAT = 1
CMD_LISTDIR = 2
# CMD 3 unused (was CMD_ILISTDIR_NEXT)
CMD_OPEN = 4
CMD_CLOSE = 5
CMD_READ = 6
CMD_WRITE = 7
CMD_MKDIR = 8
CMD_REMOVE = 9
CMD_RENAME = 10
CMD_SEEK = 11
CMD_READLINE = 12

CMD_MIN = CMD_STAT
CMD_MAX = CMD_READLINE

# Prebuilt bytes for hot path
_ESCAPE_BYTE = bytes([ESCAPE])

_SOFT_REBOOT = b'soft reboot'
_REPL_PROMPT = b'>>> '
_REBOOT_BUF_MAX = 256
_REBOOT_BUF_KEEP = 64
_LISTDIR_LIMIT = 1000  # max entries returned by CMD_LISTDIR

# MicroPython agent code injected into device
MOUNT_AGENT = """\
import sys,io,os,micropython,struct as S
_mt_si=sys.stdin.buffer
_mt_so=sys.stdout.buffer
_mt_E=0x18
_mt_cs=4096
def _mt_bg(cmd,mid=0):
 micropython.kbd_intr(-1)
 _mt_so.write(bytes([_mt_E,cmd,mid]))
 while _mt_si.read(1)[0]!=_mt_E:pass
def _mt_en():micropython.kbd_intr(3)
def _mt_r(f):return S.unpack(f,_mt_si.read(S.calcsize(f)))[0]
def _mt_w(f,v):_mt_so.write(S.pack(f,v))
def _mt_rs():
 n=_mt_r('<i')
 return _mt_si.read(n).decode() if n>0 else ''
def _mt_ws(v):
 b=v.encode();_mt_w('<i',len(b))
 if b:_mt_so.write(b)
class _mt_F(io.IOBase):
 def __init__(s,fd,txt,mode):
  s.fd=fd;s.txt=txt;s.mode=mode;s._pos=0
  if 'r' in mode or '+' in mode:
   s._rb=bytearray(_mt_cs)
   s._rn=0;s._rp=0
  else:s._rb=None
 def _refill(s):
  _mt_bg(6);_mt_w('b',s.fd);_mt_w('<i',_mt_cs)
  n=_mt_r('<i')
  if n<0:_mt_en();raise OSError(-n)
  if n>0:
   mv=memoryview(s._rb);p=0
   while p<n:
    r=_mt_si.readinto(mv[p:n])
    if r:p+=r
  _mt_en();s._rn=n;s._rp=0
 def readinto(s,buf):
  if s._rb is None:raise OSError(9)
  n=len(buf);p=0
  while p<n:
   if s._rp>=s._rn:
    s._refill()
    if s._rn<=0:break
   a=min(n-p,s._rn-s._rp)
   buf[p:p+a]=s._rb[s._rp:s._rp+a]
   s._rp+=a;p+=a
  s._pos+=p;return p
 def readline(s):
  r=bytearray()
  if s._rp<s._rn:
   i=s._rb.find(b'\\n',s._rp,s._rn)
   if i>=0:
    r+=s._rb[s._rp:i+1];s._rp=i+1
    s._pos+=len(r);return str(r,'utf8') if s.txt else bytes(r)
   r+=s._rb[s._rp:s._rn];s._rp=s._rn
  _mt_bg(12);_mt_w('b',s.fd)
  n=_mt_r('<i')
  if n<0:_mt_en();raise OSError(-n)
  if n>0:r+=_mt_si.read(n)
  _mt_en();s._pos+=len(r);return str(r,'utf8') if s.txt else bytes(r)
 def read(s,n=-1):
  if n>0:
   b=bytearray(n);d=bytes(b[:s.readinto(b)])
  else:
   p=[];b=bytearray(_mt_cs)
   while True:
    g=s.readinto(b)
    if g<=0:break
    p.append(bytes(b[:g]))
   d=b''.join(p)
  return str(d,'utf8') if s.txt else d
 def __iter__(s):
  while True:
   l=s.readline()
   if not l:break
   yield l
 def readlines(s):return list(s)
 def write(s,buf):
  b=buf.encode('utf8') if s.txt and isinstance(buf,str) else bytes(buf)
  _mt_bg(7);_mt_w('b',s.fd);_mt_w('<i',len(b))
  _mt_so.write(b);e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
  s._pos+=len(b);return len(b)
 def ioctl(s,req,arg):
  if req==4:s.close()
  elif req==11:return _mt_cs
  return 0
 def seek(s,offset,whence=0):
  if whence==1:offset+=s._pos;whence=0
  _mt_bg(11);_mt_w('b',s.fd);_mt_w('<i',offset);_mt_w('b',whence)
  pos=_mt_r('<i');_mt_en()
  if pos<0:raise OSError(-pos)
  if s._rb:s._rp=s._rn=0
  s._pos=pos;return pos
 def close(s):
  if s.fd>=0:
   _mt_bg(5);_mt_w('b',s.fd)
   e=_mt_r('b');_mt_en()
   if e<0:raise OSError(-e)
   s.fd=-1
 def __enter__(s):return s
 def __exit__(s,*_):s.close()
class RemoteFS:
 def __init__(s,mid=0):
  s.mid=mid;s._cwd='/'
 def mount(s,ro,mkfs):pass
 def umount(s):pass
 def chdir(s,p):
  if p.startswith('/'):s._cwd=p
  elif p=='..':
   s._cwd='/'.join(s._cwd.rstrip('/').split('/')[:-1]) or '/'
  else:
   s._cwd=s._cwd.rstrip('/')+'/'+p
 def getcwd(s):return s._cwd
 def _abs(s,p):
  if not p or p=='.':return s._cwd
  if p.startswith('/'):return p
  return s._cwd.rstrip('/')+'/'+p
 def stat(s,p):
  _mt_bg(1,s.mid);_mt_ws(s._abs(p))
  r=_mt_r('b')
  if r<0:_mt_en();raise OSError(-r)
  m=_mt_r('<I');sz=_mt_r('<I');mt=_mt_r('<I')
  _mt_en()
  return(m,0,0,0,0,0,sz,mt,mt,mt)
 def ilistdir(s,p):
  _mt_bg(2,s.mid);_mt_ws(s._abs(p))
  e=_mt_r('b')
  if e<0:_mt_en();raise OSError(-e)
  c=_mt_r('<i');r=[]
  for _ in range(c):
   n=_mt_rs();m=_mt_r('<I');sz=_mt_r('<I')
   r.append((n,m,0,sz))
  _mt_en()
  for x in r:yield x
 def open(s,p,mode):
  _mt_bg(4,s.mid);_mt_ws(s._abs(p));_mt_ws(mode)
  fd=_mt_r('b');_mt_en()
  if fd<0:raise OSError(-fd)
  return _mt_F(fd,'b' not in mode,mode)
 def mkdir(s,p):
  _mt_bg(8,s.mid);_mt_ws(s._abs(p))
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
 def remove(s,p):
  _mt_bg(9,s.mid);_mt_ws(s._abs(p));_mt_w('b',0)
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
 def rmdir(s,p):
  _mt_bg(9,s.mid);_mt_ws(s._abs(p));_mt_w('b',0)
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
 def rename(s,old,new):
  _mt_bg(10,s.mid);_mt_ws(s._abs(old));_mt_ws(s._abs(new))
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
def _mt_mount(mp='/remote',mid=0,cs=4096):
 global _mt_cs
 _mt_cs=cs
 try:os.umount(mp)
 except OSError:pass
 os.mount(RemoteFS(mid=mid),mp)
"""


class _VFSReader:
    """Temporary reader for VFS handler.

    Reads from buffer first, then underlying conn.

    When VfsProtocol detects a VFS command, it has already read
    data from serial port into its buffer. This reader allows the
    handler to read VFS parameters from that buffer first, and only
    read from underlying connection if buffer is exhausted.
    """

    def __init__(self, data, offset, underlying_conn):
        self._data = data
        self._offset = offset
        self._conn = underlying_conn

    def read_bytes(self, n, timeout=None):
        """Read n bytes from buffer + underlying connection"""
        result = bytearray()
        available = len(self._data) - self._offset
        if available > 0:
            chunk = min(n, available)
            result.extend(self._data[self._offset:self._offset + chunk])
            self._offset += chunk
            n -= chunk
        if n > 0:
            result.extend(self._conn.read_bytes(n, timeout))
        return bytes(result)

    def write(self, data):
        """Write directly to underlying connection"""
        return self._conn.write(data)


class MountHandler:
    """PC-side handler for VFS requests from MicroPython device.

    Receives VFS commands via escape protocol, performs filesystem operations
    on local directory, and sends responses back to device.

    Args:
        conn: Connection object with read_bytes() and write() methods.
        root: Local directory path to serve as VFS root.
        log: Optional logger for debug output.
        writable: If True, allow write operations (default: readonly).
        mpy_cross: Optional MpyCross instance for .py → .mpy compilation.
        _dismiss: Internal flag for stale VFS handling (returns ENOENT).

    Security:
        - All paths resolved via realpath() and validated against root
        - Symlinks followed but must stay within root (EACCES otherwise)
        - Write operations require explicit writable=True

    Submounts:
        Virtual paths can be added via add_submount(subpath, local_dir).
        These appear as subdirectories within the VFS namespace.
    """

    def __init__(
            self, conn, root, log=None, writable=False, mpy_cross=None,
            _dismiss=False):
        self._conn = conn
        self._dismiss = _dismiss
        if not _dismiss:
            self._root = _os.path.realpath(root)
        else:
            self._root = None
        self._log = log
        self._writable = writable
        self._mpy_cross = mpy_cross
        self._files = {}
        self._next_fd = 0
        self._free_fds = []
        self._submounts = {}  # {subpath: realpath} for virtual nested mounts
        self._dispatch = {
            CMD_STAT: self._do_stat,
            CMD_LISTDIR: self._do_listdir,
            CMD_OPEN: self._do_open,
            CMD_CLOSE: self._do_close,
            CMD_READ: self._do_read,
            CMD_WRITE: self._do_write,
            CMD_MKDIR: self._do_mkdir,
            CMD_REMOVE: self._do_remove,
            CMD_RENAME: self._do_rename,
            CMD_SEEK: self._do_seek,
            CMD_READLINE: self._do_readline,
        }

    def add_submount(self, subpath, local_dir):
        """Add virtual submount — subpath is relative to VFS root"""
        self._submounts[subpath] = _os.path.realpath(local_dir)

    def _rd_s8(self):
        return _struct.unpack('b', self._conn.read_bytes(1))[0]

    def _rd_s32(self):
        return _struct.unpack('<i', self._conn.read_bytes(4))[0]

    def _rd_str(self):
        n = self._rd_s32()
        if n <= 0:
            return ''
        return self._conn.read_bytes(n).decode('utf-8')

    def _rd_bytes(self):
        n = self._rd_s32()
        if n <= 0:
            return b''
        return self._conn.read_bytes(n)

    def _wr_s8(self, val):
        self._conn.write(_struct.pack('b', val))

    def _wr_s32(self, val):
        self._conn.write(_struct.pack('<i', val))

    def _wr_u32(self, val):
        self._conn.write(_struct.pack('<I', val))

    def _wr_bytes(self, data):
        self._wr_s32(len(data))
        if data:
            self._conn.write(data)

    def _send_stat(self, st, path=None):
        """Send stat response for successful stat"""
        mode = st.st_mode & 0xF000
        self._wr_s8(0)  # OK
        self._wr_u32(mode)  # type bits only
        self._wr_u32(st.st_size)
        self._wr_u32(int(st.st_mtime))
        if self._log and path:
            self._log.info(
                "MOUNT: STAT: '%s' -> mode=0x%04x size=%d",
                path, mode, st.st_size)

    def _resolve_path(self, path):
        """Resolve path within mount root or submount, prevent traversal"""
        if self._dismiss:
            return None
        path = path.lstrip('/')
        # Check submounts (longest prefix first to handle nested submounts)
        for subpath in sorted(
                self._submounts, key=len, reverse=True):
            if path == subpath or path.startswith(subpath + '/'):
                local_dir = self._submounts[subpath]
                remainder = path[len(subpath):].lstrip('/')
                full = _os.path.realpath(
                    _os.path.join(local_dir, remainder))
                if not full.startswith(local_dir):
                    return None
                return full
        full = _os.path.realpath(_os.path.join(self._root, path))
        if not full.startswith(self._root):
            return None
        return full

    def _path_error(self):
        """Return error code for path resolution failure.

        ENOENT for dismiss mode (stale VFS), EACCES for path traversal.
        """
        return -_errno.ENOENT if self._dismiss else -_errno.EACCES

    def dispatch(self, cmd):
        """Dispatch VFS command from device"""
        handler = self._dispatch.get(cmd)
        if handler:
            handler()

    def _is_virtual_dir(self, path):
        """Check if path is a virtual intermediate directory from submounts"""
        prefix = path.lstrip('/').rstrip('/')
        if not prefix:
            return False
        return any(
            sp.startswith(prefix + '/') for sp in self._submounts)

    def _find_mpy_path(self, mpy_path):
        """Find actual path for .mpy file request."""
        py_path = mpy_path[:-4] + '.py'
        local_py = self._resolve_path(py_path)
        if not local_py or not _os.path.isfile(local_py):
            return None
        return self._mpy_cross.find_compiled(local_py)

    def _log_stat_err(self, path, err):
        if self._log:
            self._log.info("MOUNT: STAT: '%s' -> %s", path, err)

    def _do_stat_py_compile(self, path, local):
        """Handle .py stat with mpy-cross compilation."""
        basename = _os.path.basename(local)
        if basename in BOOT_FILES:
            try:
                self._send_stat(_os.stat(local), path)
            except OSError:
                self._wr_s8(-_errno.ENOENT)
                self._log_stat_err(path, 'ENOENT')
            return

        try:
            st = _os.stat(local)
            if st.st_size == 0:
                self._send_stat(st, path)
                return
            if self._mpy_cross.compile(local):
                self._wr_s8(-_errno.ENOENT)  # redirect to .mpy
                self._log_stat_err(path, 'ENOENT (->mpy)')
            else:
                self._send_stat(st, path)  # fallback to .py
        except OSError:
            self._wr_s8(-_errno.ENOENT)
            self._log_stat_err(path, 'ENOENT')

    def _do_stat_mpy_lookup(self, path):
        """Handle .mpy stat lookup from cache."""
        mpy_local = self._find_mpy_path(path)
        if mpy_local:
            try:
                self._send_stat(_os.stat(mpy_local), path)
                return
            except OSError:
                pass
        self._wr_s8(-_errno.ENOENT)
        self._log_stat_err(path, 'ENOENT')

    def _do_stat(self):
        path = self._rd_str()
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(self._path_error())
            self._log_stat_err(path, 'ENOENT')
            return

        if self._mpy_cross:
            if path.endswith('.py'):
                self._do_stat_py_compile(path, local)
                return
            if path.endswith('.mpy'):
                self._do_stat_mpy_lookup(path)
                return

        # Normal stat
        try:
            self._send_stat(_os.stat(local), path)
        except OSError:
            if self._is_virtual_dir(path):
                self._wr_s8(0)
                self._wr_u32(0x4000)  # S_IFDIR
                self._wr_u32(0)
                self._wr_u32(0)
                if self._log:
                    self._log.info(
                        "MOUNT: STAT: '%s' -> mode=0x4000 (vdir)", path)
            else:
                self._wr_s8(-_errno.ENOENT)
                self._log_stat_err(path, 'ENOENT')

    def _listdir_entries(self, path, local):
        """Generate (name, mode, size) entries for a directory path.

        Args:
            path: VFS path (for submount matching)
            local: Already resolved local path
        """
        real_dir = False
        existing = set()
        try:
            for name in _os.listdir(local):
                full = _os.path.join(local, name)
                try:
                    st = _os.stat(full)
                    existing.add(name)
                    mode = st.st_mode & 0xF000
                    size = st.st_size if mode == 0x8000 else 0
                    yield (name, mode, size)
                except OSError:
                    pass
            real_dir = True
        except OSError:
            pass  # Virtual intermediate dir — may have submount entries
        prefix = path.lstrip('/').rstrip('/')
        for subpath in self._submounts:
            if prefix:
                if not subpath.startswith(prefix + '/'):
                    continue
                child = subpath[len(prefix) + 1:]
            else:
                child = subpath
            # Only direct children
            child_name = child.split('/')[0]
            if child_name and child_name not in existing:
                if child_name == child:
                    # Direct submount — use actual type and size
                    local_sub = self._submounts[subpath]
                    try:
                        st = _os.stat(local_sub)
                        mode = st.st_mode & 0xF000
                        size = st.st_size if mode == 0x8000 else 0
                    except OSError:
                        mode = 0x4000
                        size = 0
                else:
                    mode = 0x4000  # virtual intermediate dir
                    size = 0
                yield (child_name, mode, size)
                existing.add(child_name)

    def _do_listdir(self):
        path = self._rd_str()
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(self._path_error())
            if self._log:
                self._log.info("MOUNT: LISTDIR: '%s' -> ENOENT", path)
            return
        # Check if path exists (real dir, virtual dir, or has submount entries)
        exists = False
        try:
            _os.listdir(local)
            exists = True
        except OSError:
            pass
        if not exists:
            exists = self._is_virtual_dir(path)
        if not exists:
            # Check if any submounts would create entries
            prefix = path.lstrip('/').rstrip('/')
            for subpath in self._submounts:
                if prefix:
                    if subpath.startswith(prefix + '/'):
                        exists = True
                        break
                else:
                    exists = True
                    break
        if not exists:
            self._wr_s8(-_errno.ENOENT)
            if self._log:
                self._log.info("MOUNT: LISTDIR: '%s' -> ENOENT", path)
            return
        # Collect entries (with limit)
        entries = []
        for name, mode, size in self._listdir_entries(path, local):
            entries.append((name, mode, size))
            if len(entries) >= _LISTDIR_LIMIT:
                break
        # Send response: errno + count + entries
        self._wr_s8(0)  # OK
        self._wr_s32(len(entries))
        for name, mode, size in entries:
            self._wr_bytes(name.encode('utf-8'))
            self._wr_u32(mode)
            self._wr_u32(size)
        if self._log:
            self._log.info(
                "MOUNT: LISTDIR: '%s' -> %d entries", path, len(entries))

    def _do_open(self):
        path = self._rd_str()
        mode = self._rd_str()
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(self._path_error())
            if self._log:
                self._log.info(
                    "MOUNT: OPEN: '%s' mode=%s -> ENOENT", path, mode)
            return

        if ('w' in mode or 'a' in mode or '+' in mode) and not self._writable:
            self._wr_s8(-_errno.EROFS)
            if self._log:
                self._log.info(
                    "MOUNT: OPEN: '%s' mode=%s -> EROFS", path, mode)
            return

        # .mpy file with -m mode: redirect to prebuilt or cache
        if path.endswith('.mpy') and self._mpy_cross:
            mpy_local = self._find_mpy_path(path)
            if mpy_local:
                local = mpy_local

        try:
            # Always binary on PC side — text conversion happens on device
            bin_mode = mode if 'b' in mode else mode + 'b'
            f = open(local, bin_mode)
            if self._free_fds:
                fd = self._free_fds.pop()
            else:
                fd = self._next_fd
                self._next_fd += 1
            self._files[fd] = f
            self._wr_s8(fd)
            if self._log:
                self._log.info(
                    "MOUNT: OPEN: '%s' mode=%s -> fd=%d", path, mode, fd)
        except OSError as e:
            self._wr_s8(-e.errno if e.errno else -_errno.EIO)
            if self._log:
                self._log.info(
                    "MOUNT: OPEN: '%s' mode=%s -> errno=%d",
                    path, mode, e.errno)

    def _do_close(self):
        fd = self._rd_s8()
        f = self._files.pop(fd, None)
        if f:
            try:
                f.close()
                self._free_fds.append(fd)
                self._wr_s8(0)  # success
                if self._log:
                    self._log.info("MOUNT: CLOSE: fd=%d -> OK", fd)
            except OSError as e:
                self._wr_s8(-e.errno)
                if self._log:
                    self._log.info(
                        "MOUNT: CLOSE: fd=%d -> errno=%d", fd, e.errno)
        else:
            self._wr_s8(-_errno.EBADF)  # invalid fd
            if self._log:
                self._log.info("MOUNT: CLOSE: fd=%d -> EBADF", fd)

    def _do_read(self):
        fd = self._rd_s8()
        n = self._rd_s32()
        f = self._files.get(fd)
        if f is None:
            self._wr_s32(-_errno.EBADF)
            if self._log:
                self._log.info("MOUNT: READ: fd=%d n=%d -> EBADF", fd, n)
            return
        try:
            data = f.read(n)
            self._wr_bytes(data if data else b'')
            if self._log:
                self._log.info(
                    "MOUNT: READ: fd=%d n=%d -> %dB", fd, n,
                    len(data) if data else 0)
        except OSError as e:
            self._wr_s32(-e.errno if e.errno else -_errno.EIO)
            if self._log:
                self._log.info(
                    "MOUNT: READ: fd=%d n=%d -> errno=%d", fd, n, e.errno)

    def _do_write(self):
        fd = self._rd_s8()
        data = self._rd_bytes()
        n = len(data)
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            if self._log:
                self._log.info("MOUNT: WRITE: fd=%d n=%d -> EROFS", fd, n)
            return
        f = self._files.get(fd)
        if f is None:
            self._wr_s8(-_errno.EBADF)
            if self._log:
                self._log.info("MOUNT: WRITE: fd=%d n=%d -> EBADF", fd, n)
            return
        try:
            f.write(data)
            self._wr_s8(0)
            if self._log:
                self._log.info("MOUNT: WRITE: fd=%d n=%d -> OK", fd, n)
        except OSError as e:
            self._wr_s8(-e.errno)
            if self._log:
                self._log.info(
                    "MOUNT: WRITE: fd=%d n=%d -> errno=%d", fd, n, e.errno)

    def _do_mkdir(self):
        path = self._rd_str()
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            if self._log:
                self._log.info("MOUNT: MKDIR: '%s' -> EROFS", path)
            return
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(self._path_error())
            if self._log:
                self._log.info("MOUNT: MKDIR: '%s' -> ENOENT", path)
            return
        try:
            _os.makedirs(local, exist_ok=True)
            self._wr_s8(0)
            if self._log:
                self._log.info("MOUNT: MKDIR: '%s' -> OK", path)
        except OSError as e:
            self._wr_s8(-e.errno)
            if self._log:
                self._log.info(
                    "MOUNT: MKDIR: '%s' -> errno=%d", path, e.errno)

    def _do_remove(self):
        path = self._rd_str()
        recursive = self._rd_s8()
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            if self._log:
                self._log.info(
                    "MOUNT: REMOVE: '%s' rec=%d -> EROFS", path, recursive)
            return
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(self._path_error())
            if self._log:
                self._log.info(
                    "MOUNT: REMOVE: '%s' rec=%d -> ENOENT", path, recursive)
            return
        try:
            if recursive:
                if _os.path.isdir(local):
                    _shutil.rmtree(local)
                else:
                    _os.remove(local)
            else:
                if _os.path.isdir(local):
                    _os.rmdir(local)
                else:
                    _os.remove(local)
            self._wr_s8(0)
            if self._log:
                self._log.info(
                    "MOUNT: REMOVE: '%s' rec=%d -> OK", path, recursive)
        except OSError as e:
            self._wr_s8(-e.errno)
            if self._log:
                self._log.info(
                    "MOUNT: REMOVE: '%s' rec=%d -> errno=%d",
                    path, recursive, e.errno)

    def _do_rename(self):
        old_path = self._rd_str()
        new_path = self._rd_str()
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            if self._log:
                self._log.info(
                    "MOUNT: RENAME: '%s' -> '%s' EROFS", old_path, new_path)
            return
        old_local = self._resolve_path(old_path)
        new_local = self._resolve_path(new_path)
        if old_local is None or new_local is None:
            self._wr_s8(self._path_error())
            if self._log:
                self._log.info(
                    "MOUNT: RENAME: '%s' -> '%s' ENOENT", old_path, new_path)
            return
        try:
            _os.replace(old_local, new_local)
            self._wr_s8(0)
            if self._log:
                self._log.info(
                    "MOUNT: RENAME: '%s' -> '%s' OK", old_path, new_path)
        except OSError as e:
            self._wr_s8(-e.errno)
            if self._log:
                self._log.info(
                    "MOUNT: RENAME: '%s' -> '%s' errno=%d",
                    old_path, new_path, e.errno)

    def _do_seek(self):
        fd = self._rd_s8()
        offset = self._rd_s32()
        whence = self._rd_s8()
        f = self._files.get(fd)
        if not f:
            self._wr_s32(-_errno.EBADF)
            if self._log:
                self._log.info(
                    "MOUNT: SEEK: fd=%d off=%d wh=%d -> EBADF",
                    fd, offset, whence)
            return
        try:
            pos = f.seek(offset, whence)
            self._wr_s32(pos)
            if self._log:
                self._log.info(
                    "MOUNT: SEEK: fd=%d off=%d wh=%d -> pos=%d",
                    fd, offset, whence, pos)
        except OSError as e:
            self._wr_s32(-e.errno)
            if self._log:
                self._log.info(
                    "MOUNT: SEEK: fd=%d off=%d wh=%d -> errno=%d",
                    fd, offset, whence, e.errno)

    def _do_readline(self):
        fd = self._rd_s8()
        f = self._files.get(fd)
        if f is None:
            self._wr_s32(-_errno.EBADF)
            if self._log:
                self._log.info("MOUNT: READLINE: fd=%d -> EBADF", fd)
            return
        try:
            line = f.readline()
            self._wr_bytes(line if line else b'')
            if self._log:
                self._log.info(
                    "MOUNT: READLINE: fd=%d -> %dB", fd, len(line) if line else 0)
        except OSError as e:
            self._wr_s32(-e.errno if e.errno else -_errno.EIO)
            if self._log:
                self._log.info(
                    "MOUNT: READLINE: fd=%d -> errno=%d", fd, e.errno)

    def close_all(self):
        """Close all open file handles"""
        for f in self._files.values():
            try:
                f.close()
            except OSError:
                pass
        self._files.clear()
        self._next_fd = 0
        self._free_fds.clear()

    def __del__(self):
        """Cleanup: close all files on destruction"""
        self.close_all()


class VfsProtocol:
    """VFS protocol handler - state machine for processing VFS commands.

    Receives data INCLUDING the ESCAPE byte. Verifies ESCAPE, processes CMD,
    MID, and parameters, dispatches to appropriate handler, returns leftover.

    Protocol with Conn:
        - Conn detects ESCAPE (0x18), activates VfsProtocol
        - Conn sends data FROM ESCAPE (inclusive) to process()
        - process() returns:
            None  - need more data, keep sending
            b''   - done, no leftover data
            bytes - done, return these to REPL output

    State machine:
        STATE_ESCAPE -> STATE_CMD -> STATE_MID -> STATE_DISPATCH -> STATE_ESCAPE
    """

    STATE_ESCAPE = 0
    STATE_CMD = 1
    STATE_MID = 2
    STATE_DISPATCH = 3

    def __init__(self, conn, remount_fn=None, log=None):
        self._conn = conn
        self._handlers = {}  # {mid: MountHandler}
        self._remount_fn = remount_fn
        self._log = log
        self._buf = b''
        self._state = self.STATE_ESCAPE
        self._cmd = None
        self._mid = None
        self._reboot_buf = b''
        self._needs_remount = False
        self._dismiss_handler = None
        self._dispatching = False  # True during handler.dispatch()
        # Register self in conn for ESCAPE byte
        conn.register_escape_handler(ESCAPE, self)

    def add_handler(self, mid, handler):
        """Add mount handler for given mount ID"""
        self._handlers[mid] = handler

    @property
    def pending(self):
        """True if there is pending data or in middle of command"""
        return bool(self._buf) or self._state != self.STATE_ESCAPE

    @property
    def busy(self):
        """True if in middle of processing VFS command"""
        return self._state != self.STATE_ESCAPE

    def _get_dismiss_handler(self):
        """Get or create dismiss handler for unknown MIDs."""
        if self._dismiss_handler is None:
            self._dismiss_handler = MountHandler(
                self._conn, root=None, _dismiss=True)
        return self._dismiss_handler

    def process(self, data):
        """Process VFS command data (including ESCAPE byte).

        Arguments:
            data: Bytes starting with ESCAPE (or None to process pending only)

        Returns:
            None  - need more data
            b''   - done, no leftover
            bytes - done, these are leftover for REPL
        """
        # If we're in the middle of dispatch, return data to conn._buffer
        # (dispatch reads raw data via _VFSReader → conn.read_bytes)
        if self._dispatching:
            return data or b''

        if data:
            self._buf += data

        while self._buf or self._state == self.STATE_DISPATCH:
            if self._state == self.STATE_ESCAPE:
                if self._buf[0] != ESCAPE:
                    # Not our ESCAPE - return all data back
                    if self._log:
                        self._log.debug(
                            "VFS: expected ESCAPE, got 0x%02x", self._buf[0])
                    result = self._buf
                    self._buf = b''
                    return result
                self._buf = self._buf[1:]
                self._state = self.STATE_CMD

            elif self._state == self.STATE_CMD:
                cmd = self._buf[0]
                if cmd < CMD_MIN or cmd > CMD_MAX:
                    # Invalid CMD - return ESCAPE + data back to REPL
                    if self._log:
                        self._log.debug("VFS: invalid cmd=%d", cmd)
                    result = bytes([ESCAPE]) + self._buf
                    self._buf = b''
                    self._state = self.STATE_ESCAPE
                    return result
                self._cmd = cmd
                self._buf = self._buf[1:]
                self._state = self.STATE_MID

            elif self._state == self.STATE_MID:
                self._mid = self._buf[0]
                self._buf = self._buf[1:]
                self._state = self.STATE_DISPATCH

            elif self._state == self.STATE_DISPATCH:
                # Get handler
                handler = self._handlers.get(self._mid)
                if not handler:
                    handler = self._get_dismiss_handler()

                # Send ACK
                self._conn.write(_ESCAPE_BYTE)

                # Read params and dispatch using _VFSReader
                reader = _VFSReader(self._buf, 0, self._conn)
                orig_conn = handler._conn
                handler._conn = reader
                self._dispatching = True
                try:
                    handler.dispatch(self._cmd)
                finally:
                    self._dispatching = False
                    handler._conn = orig_conn

                # Get remaining data from reader buffer
                remaining = bytes(reader._data[reader._offset:])
                self._buf = b''
                self._state = self.STATE_ESCAPE

                if remaining:
                    # Leftover data - return to Conn for REPL/next ESCAPE
                    return remaining
                return b''

        # Buffer empty, need more data
        return None

    def check_reboot(self, data):
        """Check REPL output for soft reboot marker.

        Call this with REPL output data (not VFS data).
        """
        if self._remount_fn is None or not data:
            return
        if self._needs_remount:
            self._reboot_buf += data
            if _REPL_PROMPT in self._reboot_buf:
                self._needs_remount = False
                self._reboot_buf = b''
                self._remount_fn()
        else:
            self._reboot_buf += data
            if len(self._reboot_buf) > _REBOOT_BUF_MAX:
                self._reboot_buf = self._reboot_buf[-_REBOOT_BUF_KEEP:]
            if _SOFT_REBOOT in self._reboot_buf:
                self._needs_remount = True
                self._reboot_buf = b''

    def close_all(self):
        """Close all handlers"""
        for handler in self._handlers.values():
            handler.close_all()

