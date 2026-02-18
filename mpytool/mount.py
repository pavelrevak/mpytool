"""MicroPython tool: mount local directory on device as VFS

## Architecture

Three-layer architecture:
1. **Agent (MicroPython)**: VFS driver on device (~4.0KB, 149 lines)
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

3. **Intercept (PC)**: Transparent proxy on serial connection
   (ConnIntercept class)
   - Intercepts escape sequences (0x18) and dispatches to handler
   - Passes through all other data (REPL I/O)
   - Detects soft reboot and triggers remount

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

| CMD | Name    | Device → PC Params    | PC → Device Response |
|-----|---------|-----------------------|----------------------|
| 1   | STAT    | path (str)            | errno, mode, size,   |
|     |         |                       | mtime                |
| 2   | LISTDIR | path (str)            | count, entries       |
| 3   | OPEN    | path, mode (str)      | fd (s8)              |
| 4   | CLOSE   | fd (s8)               | errno (s8)           |
| 5   | READ    | fd, count (s32)       | length, data         |
| 6   | WRITE   | fd, length, data      | errno (s8)           |
| 7   | MKDIR   | path (str)            | errno (s8)           |
| 8   | REMOVE  | path, recursive (s8)  | errno (s8)           |
| 9   | RENAME  | old_path, new_path    | errno (s8)           |
|     |         | (str)                 |                      |
| 10  | SEEK    | fd (s8), offset (s32),| position (s32)       |
|     |         | whence (s8)           |                      |
| 11  | READLINE| fd (s8)               | length, data         |

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

7. Device sends: \x18 \x03 \x00        # CMD_OPEN (3), mid=0
8. PC sends: \x18                      # ACK
9. Device sends: path + mode 'r'
10. PC responds: \x00                  # fd=0
11. Device unblocks, creates _mt_F(fd=0)

12. Device sends: \x18 \x05 \x00       # CMD_READ (5), mid=0
13. PC sends: \x18                     # ACK
14. Device sends: \x00 \xff\xff\xff\xff  # fd=0, count=-1 (read all)
15. PC responds: \x0c\x00\x00\x00 + data  # length=12, data bytes
16. Device reads data, unblocks

17. Device sends: \x18 \x04 \x00       # CMD_CLOSE (4), mid=0
18. PC sends: \x18                     # ACK
19. Device sends: \x00                 # fd=0
20. PC responds: \x00                  # errno=0 (OK)
21. Device unblocks, fd closed
```

## Soft Reboot Handling

When device soft resets (Ctrl+D):
1. ConnIntercept detects "soft reboot" marker in output stream
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

from mpytool.conn import Conn
from mpytool.mpy_comm import MpyError
from mpytool.mpy_cross import BOOT_FILES

# Protocol constants
ESCAPE = 0x18  # CAN / Ctrl+X
CMD_STAT = 1
CMD_LISTDIR = 2
CMD_OPEN = 3
CMD_CLOSE = 4
CMD_READ = 5
CMD_WRITE = 6
CMD_MKDIR = 7
CMD_REMOVE = 8
CMD_RENAME = 9
CMD_SEEK = 10
CMD_READLINE = 11

CMD_MIN = CMD_STAT
CMD_MAX = CMD_READLINE

# Prebuilt bytes for hot path
_ESCAPE_BYTE = bytes([ESCAPE])

_SOFT_REBOOT = b'soft reboot'
_REPL_PROMPT = b'>>> '

# MicroPython agent code injected into device
# CHUNK_SIZE is replaced with actual value before injection
MOUNT_AGENT = """\
import sys,io,os,micropython,struct as S
_mt_si=sys.stdin.buffer
_mt_so=sys.stdout.buffer
_mt_E=0x18
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
   s._rb=bytearray(CHUNK_SIZE)
   s._rn=0;s._rp=0
  else:s._rb=None
 def _refill(s):
  _mt_bg(5);_mt_w('b',s.fd);_mt_w('<i',CHUNK_SIZE)
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
  _mt_bg(11);_mt_w('b',s.fd)
  n=_mt_r('<i')
  if n<0:_mt_en();raise OSError(-n)
  if n>0:r+=_mt_si.read(n)
  _mt_en();s._pos+=len(r);return str(r,'utf8') if s.txt else bytes(r)
 def read(s,n=-1):
  if n>0:
   b=bytearray(n);d=bytes(b[:s.readinto(b)])
  else:
   p=[];b=bytearray(CHUNK_SIZE)
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
  _mt_bg(6);_mt_w('b',s.fd);_mt_w('<i',len(b))
  _mt_so.write(b);e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
  s._pos+=len(b);return len(b)
 def ioctl(s,req,arg):
  if req==4:s.close()
  elif req==11:return CHUNK_SIZE
  return 0
 def seek(s,offset,whence=0):
  if whence==1:offset+=s._pos;whence=0
  _mt_bg(10);_mt_w('b',s.fd);_mt_w('<i',offset);_mt_w('b',whence)
  pos=_mt_r('<i');_mt_en()
  if pos<0:raise OSError(-pos)
  if s._rb:s._rp=s._rn=0
  s._pos=pos;return pos
 def close(s):
  if s.fd>=0:
   _mt_bg(4);_mt_w('b',s.fd)
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
  n=_mt_r('<i')
  if n<0:_mt_en();raise OSError(-n)
  E=[]
  for _ in range(n):E.append((_mt_rs(),_mt_r('<I'),0))
  _mt_en()
  for e in E:yield e
 def open(s,p,mode):
  _mt_bg(3,s.mid);_mt_ws(s._abs(p));_mt_ws(mode)
  fd=_mt_r('b');_mt_en()
  if fd<0:raise OSError(-fd)
  return _mt_F(fd,'b' not in mode,mode)
 def mkdir(s,p):
  _mt_bg(7,s.mid);_mt_ws(s._abs(p))
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
 def remove(s,p):
  _mt_bg(8,s.mid);_mt_ws(s._abs(p));_mt_w('b',0)
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
 def rmdir(s,p):
  _mt_bg(8,s.mid);_mt_ws(s._abs(p));_mt_w('b',0)
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
 def rename(s,old,new):
  _mt_bg(9,s.mid);_mt_ws(s._abs(old));_mt_ws(s._abs(new))
  e=_mt_r('b');_mt_en()
  if e<0:raise OSError(-e)
def _mt_mount(mp='/remote',mid=0):
 try:os.umount(mp)
 except:pass
 os.mount(RemoteFS(mid=mid),mp)
"""


class _VFSReader:
    """Temporary reader for VFS handler.

    Reads from buffer first, then underlying conn.

    When ConnIntercept detects a VFS command, it has already read
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
    """PC-side handler for VFS requests from device"""

    def __init__(
            self, conn, root, log=None, writable=False, mpy_cross=None):
        self._conn = conn
        self._root = _os.path.realpath(root)
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

    def _send_stat(self, st):
        """Send stat response for successful stat"""
        self._wr_s8(0)  # OK
        self._wr_u32(st.st_mode & 0xF000)  # type bits only
        self._wr_u32(st.st_size)
        self._wr_u32(int(st.st_mtime))

    def _resolve_path(self, path):
        """Resolve path within mount root or submount, prevent traversal"""
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

    def _do_stat(self):
        path = self._rd_str()
        if self._log:
            self._log.info("MOUNT: STAT: '%s'", path)
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(-_errno.EACCES)
            return

        # .py file with -m mode
        if path.endswith('.py') and self._mpy_cross:
            basename = _os.path.basename(local)
            # Always use .py for boot files
            if basename in BOOT_FILES:
                try:
                    self._send_stat(_os.stat(local))
                except OSError:
                    self._wr_s8(-_errno.ENOENT)
                return

            try:
                st = _os.stat(local)
                # Empty file - no need to compile
                if st.st_size == 0:
                    self._send_stat(st)
                    return
                cache_path = self._mpy_cross.compile(local)
                if cache_path:
                    # Compilation OK → redirect to .mpy
                    self._wr_s8(-_errno.ENOENT)
                else:
                    # Compilation failed → fallback to .py
                    self._send_stat(st)
            except OSError:
                self._wr_s8(-_errno.ENOENT)
            return

        # .mpy file with -m mode
        if path.endswith('.mpy') and self._mpy_cross:
            mpy_local = self._find_mpy_path(path)
            if mpy_local:
                try:
                    self._send_stat(_os.stat(mpy_local))
                    return
                except OSError:
                    pass
            self._wr_s8(-_errno.ENOENT)
            return

        # Normal stat without -m mode
        try:
            self._send_stat(_os.stat(local))
        except OSError:
            if self._is_virtual_dir(path):
                self._wr_s8(0)
                self._wr_u32(0x4000)  # S_IFDIR
                self._wr_u32(0)
                self._wr_u32(0)
            else:
                self._wr_s8(-_errno.ENOENT)

    def _do_listdir(self):
        path = self._rd_str()
        if self._log:
            self._log.info("MOUNT: LISTDIR: '%s'", path)
        local = self._resolve_path(path)
        if local is None:
            self._wr_s32(-_errno.EACCES)
            return
        entries = []
        real_dir = False
        try:
            for name in _os.listdir(local):
                full = _os.path.join(local, name)
                try:
                    st = _os.stat(full)
                    entries.append((name, st.st_mode & 0xF000))
                except OSError:
                    pass
            real_dir = True
        except OSError:
            pass  # Virtual intermediate dir — may have submount entries
        prefix = path.lstrip('/').rstrip('/')
        existing = {name for name, _ in entries}
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
                    # Direct submount — use actual type
                    local_sub = self._submounts[subpath]
                    mode = 0x4000 if _os.path.isdir(local_sub) else 0x8000
                else:
                    mode = 0x4000  # virtual intermediate dir
                entries.append((child_name, mode))
                existing.add(child_name)
        if not entries and not real_dir and not self._is_virtual_dir(path):
            self._wr_s32(-_errno.ENOENT)
            return
        self._wr_s32(len(entries))
        for name, mode in entries:
            self._wr_bytes(name.encode('utf-8'))
            self._wr_u32(mode)

    def _do_open(self):
        path = self._rd_str()
        mode = self._rd_str()
        if self._log:
            self._log.info("MOUNT: OPEN: '%s' mode=%s", path, mode)
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(-_errno.EACCES)
            return

        if ('w' in mode or 'a' in mode or '+' in mode) and not self._writable:
            self._wr_s8(-_errno.EROFS)
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
        except OSError:
            self._wr_s8(-_errno.ENOENT)

    def _do_close(self):
        fd = self._rd_s8()
        if self._log:
            self._log.info("MOUNT: CLOSE: fd=%d", fd)
        f = self._files.pop(fd, None)
        if f:
            try:
                f.close()
                self._free_fds.append(fd)
                self._wr_s8(0)  # success
            except OSError as e:
                self._wr_s8(-e.errno)
        else:
            self._wr_s8(-_errno.EBADF)  # invalid fd

    def _do_read(self):
        fd = self._rd_s8()
        n = self._rd_s32()
        if self._log:
            self._log.info("MOUNT: READ: fd=%d n=%d", fd, n)
        f = self._files.get(fd)
        if f is None:
            self._wr_s32(-_errno.EBADF)
            return
        data = f.read(n)
        self._wr_bytes(data if data else b'')

    def _do_write(self):
        fd = self._rd_s8()
        data = self._rd_bytes()
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            return
        if self._log:
            self._log.info("MOUNT: WRITE: fd=%d n=%d", fd, len(data))
        f = self._files.get(fd)
        if f is None:
            self._wr_s8(-_errno.EBADF)
            return
        try:
            f.write(data)
            self._wr_s8(0)
        except OSError as e:
            self._wr_s8(-e.errno)

    def _do_mkdir(self):
        path = self._rd_str()
        if self._log:
            self._log.info("MOUNT: MKDIR: '%s'", path)
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            return
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(-_errno.EACCES)
            return
        try:
            _os.makedirs(local, exist_ok=True)
            self._wr_s8(0)
        except OSError as e:
            self._wr_s8(-e.errno)

    def _do_remove(self):
        path = self._rd_str()
        recursive = self._rd_s8()
        if self._log:
            self._log.info(
                "MOUNT: REMOVE: '%s' recursive=%d", path, recursive)
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            return
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(-_errno.EACCES)
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
        except OSError as e:
            self._wr_s8(-e.errno)

    def _do_rename(self):
        old_path = self._rd_str()
        new_path = self._rd_str()
        if self._log:
            self._log.info(
                "MOUNT: RENAME: '%s' -> '%s'", old_path, new_path)
        if not self._writable:
            self._wr_s8(-_errno.EROFS)
            return
        old_local = self._resolve_path(old_path)
        new_local = self._resolve_path(new_path)
        if old_local is None or new_local is None:
            self._wr_s8(-_errno.EACCES)
            return
        try:
            _os.replace(old_local, new_local)
            self._wr_s8(0)
        except OSError as e:
            self._wr_s8(-e.errno)

    def _do_seek(self):
        fd = self._rd_s8()
        offset = self._rd_s32()
        whence = self._rd_s8()
        if self._log:
            self._log.info(
                "MOUNT: SEEK: fd=%d offset=%d whence=%d", fd, offset, whence)
        f = self._files.get(fd)
        if not f:
            self._wr_s32(-_errno.EBADF)
            return
        try:
            pos = f.seek(offset, whence)
            self._wr_s32(pos)
        except OSError as e:
            self._wr_s32(-e.errno)

    def _do_readline(self):
        fd = self._rd_s8()
        if self._log:
            self._log.info("MOUNT: READLINE: fd=%d", fd)
        f = self._files.get(fd)
        if f is None:
            self._wr_s32(-_errno.EBADF)
            return
        line = f.readline()
        self._wr_bytes(line if line else b'')

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


class ConnIntercept(Conn):
    """Transparent connection proxy that intercepts VFS protocol messages.

    Sits between the real connection (serial/socket) and the upper layers
    (MpyComm, Terminal). Intercepts 0x18 escape sequences from the device,
    dispatches them to MountHandler, and passes everything else through.
    """

    def __init__(self, conn, handlers, remount_fn=None, log=None):
        super().__init__(log=log)
        self._conn = conn
        self._handlers = handlers  # {mid: MountHandler}
        self._remount_fn = remount_fn
        self._pending = b''
        self._busy = False
        self._reboot_buf = b''
        self._needs_remount = False

    def add_handler(self, mid, handler):
        """Add mount handler for given mount ID"""
        self._handlers[mid] = handler

    @property
    def fd(self):
        return self._conn.fd

    @property
    def busy(self):
        return self._busy

    def _has_data(self, timeout=0):
        if self._pending:
            return True
        return self._conn._has_data(timeout)

    def _handle_vfs_command(self, data, i, cmd, mid):
        """Handle VFS command. Returns new position or None to break parsing.
        """
        handler = self._handlers.get(mid)
        if not handler:
            # No handler for this MID — skip escape sequence
            return i + 3

        # Process VFS command
        self._busy = True
        try:
            self._conn.write(_ESCAPE_BYTE)
            # Create buffered reader for VFS parameters
            # (parameters may already be in our data buffer)
            reader = _VFSReader(data, i + 3, self._conn)
            # Temporarily replace handler's connection
            orig_conn = handler._conn
            handler._conn = reader
            try:
                handler.dispatch(cmd)
            finally:
                handler._conn = orig_conn
            # Update parse position to skip consumed VFS data
            # If handler read beyond our buffer, stop parsing
            # (remaining data will be in underlying conn's buffer)
            new_i = reader._offset
            if new_i > len(data):
                return None  # Signal break
            return new_i
        finally:
            self._busy = False

    def _process_escape_sequences(self, data, got_new_data):
        """Parse data, intercept VFS commands, return REPL output."""
        out = bytearray()
        i = 0
        while i < len(data):
            if data[i] == ESCAPE:
                # Need at least 2 more bytes (CMD + MID)
                if i + 2 >= len(data):
                    # Partial escape — save and wait for more data
                    self._pending = bytes(data[i:])
                    break

                cmd = data[i + 1]
                mid = data[i + 2]

                # Valid VFS command?
                if CMD_MIN <= cmd <= CMD_MAX:
                    new_i = self._handle_vfs_command(data, i, cmd, mid)
                    if new_i is None:
                        break  # Handler consumed beyond buffer
                    i = new_i
                    continue
                # Invalid command — fall through to append byte

            # Default: append byte and advance
            out.append(data[i])
            i += 1

        return bytes(out) if out else (b'' if got_new_data else None)

    def _read_available(self):
        raw = self._conn._read_available()
        if raw:
            data = self._pending + raw
            self._pending = b''
        elif self._pending:
            data = self._pending
            self._pending = b''
        else:
            return None

        got_new_data = bool(raw)

        # Fast path: no escape byte
        if ESCAPE not in data:
            self._check_reboot(data)
            return data

        # Slow path: parse and intercept VFS commands
        result = self._process_escape_sequences(data, got_new_data)
        if result:
            self._check_reboot(result)
        return result

    def _check_reboot(self, data):
        """Detect 'soft reboot' in output stream"""
        if self._remount_fn is None:
            return
        if self._needs_remount:
            # Look for REPL prompt after reboot
            self._reboot_buf += data
            if _REPL_PROMPT in self._reboot_buf:
                self._needs_remount = False
                self._reboot_buf = b''
                self._remount_fn()
        else:
            # Check if this data contains soft reboot marker
            # Buffer last N bytes to handle marker split across reads
            self._reboot_buf += data
            if len(self._reboot_buf) > 256:
                self._reboot_buf = self._reboot_buf[-64:]
            if _SOFT_REBOOT in self._reboot_buf:
                self._needs_remount = True
                self._reboot_buf = b''

    def _write_raw(self, data):
        if self._busy:
            raise MpyError("connection busy (VFS request in progress)")
        return self._conn._write_raw(data)

    def close(self):
        for handler in self._handlers.values():
            handler.close_all()
        self._conn.close()

    def hard_reset(self):
        self._conn.hard_reset()

    def reset_to_bootloader(self):
        self._conn.reset_to_bootloader()

    def reconnect(self, timeout=None):
        self._conn.reconnect(timeout)
