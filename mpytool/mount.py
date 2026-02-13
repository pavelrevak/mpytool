"""MicroPython tool: mount local directory on device as VFS

Phase 1: readonly mount with STAT, LISTDIR, OPEN, CLOSE, READ commands.
"""

import errno as _errno
import os as _os
import struct as _struct

from mpytool.conn import Conn
from mpytool.mpy_comm import MpyError

# Protocol constants
ESCAPE = 0x18  # CAN / Ctrl+X
CMD_STAT = 1
CMD_LISTDIR = 2
CMD_OPEN = 3
CMD_CLOSE = 4
CMD_READ = 5

# Valid command range for readonly mode
CMD_MIN = CMD_STAT
CMD_MAX = CMD_READ

# Prebuilt bytes for hot path
_ESCAPE_BYTE = bytes([ESCAPE])

# Soft reboot detection
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
def _mt_rbi(buf,n):
 mv=memoryview(buf);p=0
 while p<n:
  r=_mt_si.readinto(mv[p:n])
  if r:p+=r
class _mt_RF(io.IOBase):
 def __init__(s,fd,txt):
  s.fd=fd;s.txt=txt
  s._rb=bytearray(CHUNK_SIZE)
  s._rn=0;s._rp=0
 def _refill(s):
  _mt_bg(5);_mt_w('b',s.fd);_mt_w('<i',CHUNK_SIZE)
  n=_mt_r('<i')
  if n>0:_mt_rbi(s._rb,n)
  _mt_en();s._rn=n;s._rp=0
 def readinto(s,buf):
  n=len(buf)
  if n>=CHUNK_SIZE:
   _mt_bg(5);_mt_w('b',s.fd);_mt_w('<i',n)
   g=_mt_r('<i')
   if g>0:_mt_rbi(buf,g)
   _mt_en()
   return g if g>0 else 0
  if s._rp>=s._rn:
   s._refill()
   if s._rn<=0:return 0
  a=min(n,s._rn-s._rp)
  buf[:a]=s._rb[s._rp:s._rp+a]
  s._rp+=a
  return a
 def readline(s):
  p=[]
  while True:
   if s._rp>=s._rn:
    s._refill()
    if s._rn<=0:break
   i=s._rb.find(b'\\n',s._rp,s._rn)
   if i>=0:
    p.append(bytes(s._rb[s._rp:i+1]))
    s._rp=i+1
    break
   p.append(bytes(s._rb[s._rp:s._rn]))
   s._rp=s._rn
  d=b''.join(p)
  return str(d,'utf8') if s.txt else d
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
 def ioctl(s,req,arg):
  if req==4:s.close()
  elif req==11:return CHUNK_SIZE
  return 0
 def close(s):
  if s.fd>=0:
   _mt_bg(4);_mt_w('b',s.fd);_mt_en()
   s.fd=-1
class _mt_FS:
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
  return _mt_RF(fd,'b' not in mode)
def _mt_mount(mp='/remote'):
 try:os.umount(mp)
 except:pass
 os.mount(_mt_FS(),mp);os.chdir(mp)
 if 'lib' not in sys.path:sys.path.insert(2,'lib')
"""


class MountHandler:
    """PC-side handler for VFS requests from device"""

    def __init__(self, conn, root, log=None):
        self._conn = conn
        self._root = _os.path.realpath(root)
        self._log = log
        self._files = {}
        self._next_fd = 0
        self._dispatch = {
            CMD_STAT: self._do_stat,
            CMD_LISTDIR: self._do_listdir,
            CMD_OPEN: self._do_open,
            CMD_CLOSE: self._do_close,
            CMD_READ: self._do_read,
        }

    # -- read/write primitives --

    def _rd_s8(self):
        return _struct.unpack('b', self._conn.read_bytes(1))[0]

    def _rd_s32(self):
        return _struct.unpack('<i', self._conn.read_bytes(4))[0]

    def _rd_str(self):
        n = self._rd_s32()
        if n <= 0:
            return ''
        return self._conn.read_bytes(n).decode('utf-8')

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

    # -- path security --

    def _resolve_path(self, path):
        """Resolve path within mount root, prevent traversal"""
        # Normalize: remove leading / (device paths are absolute, local are relative to root)
        path = path.lstrip('/')
        full = _os.path.realpath(_os.path.join(self._root, path))
        if not full.startswith(self._root):
            return None  # path traversal attempt
        return full

    # -- command handlers --

    def dispatch(self, cmd):
        """Dispatch VFS command from device"""
        handler = self._dispatch.get(cmd)
        if handler:
            handler()

    def _do_stat(self):
        path = self._rd_str()
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(-_errno.EACCES)
            return
        try:
            st = _os.stat(local)
            self._wr_s8(0)  # OK
            self._wr_u32(st.st_mode & 0xF000)  # type bits only
            self._wr_u32(st.st_size)
            self._wr_u32(int(st.st_mtime))
        except OSError:
            self._wr_s8(-_errno.ENOENT)

    def _do_listdir(self):
        path = self._rd_str()
        local = self._resolve_path(path)
        if local is None:
            self._wr_s32(-_errno.EACCES)
            return
        try:
            entries = []
            for name in _os.listdir(local):
                full = _os.path.join(local, name)
                try:
                    st = _os.stat(full)
                    entries.append((name, st.st_mode & 0xF000))
                except OSError:
                    pass
            self._wr_s32(len(entries))
            for name, mode in entries:
                self._wr_bytes(name.encode('utf-8'))
                self._wr_u32(mode)
        except OSError:
            self._wr_s32(-_errno.ENOENT)

    def _do_open(self):
        path = self._rd_str()
        mode = self._rd_str()
        local = self._resolve_path(path)
        if local is None:
            self._wr_s8(-_errno.EACCES)
            return
        try:
            # Always binary on PC side — text conversion happens on device
            bin_mode = mode if 'b' in mode else mode + 'b'
            f = open(local, bin_mode)
            fd = self._next_fd
            self._next_fd += 1
            self._files[fd] = f
            self._wr_s8(fd)
        except OSError:
            self._wr_s8(-_errno.ENOENT)

    def _do_close(self):
        fd = self._rd_s8()
        f = self._files.pop(fd, None)
        if f:
            f.close()
        # No response (fire-and-forget)

    def _do_read(self):
        fd = self._rd_s8()
        n = self._rd_s32()
        f = self._files.get(fd)
        if f is None:
            self._wr_s32(0)
            return
        data = f.read(n)
        self._wr_bytes(data if data else b'')

    def close_all(self):
        """Close all open file handles"""
        for f in self._files.values():
            try:
                f.close()
            except OSError:
                pass
        self._files.clear()
        self._next_fd = 0


class ConnIntercept(Conn):
    """Transparent connection proxy that intercepts VFS protocol messages.

    Sits between the real connection (serial/socket) and the upper layers
    (MpyComm, Terminal). Intercepts 0x18 escape sequences from the device,
    dispatches them to MountHandler, and passes everything else through.
    """

    def __init__(self, conn, handler, remount_fn=None, log=None):
        super().__init__(log=log)
        self._conn = conn
        self._handler = handler
        self._remount_fn = remount_fn
        self._pending = b''
        self._busy = False
        # Soft reboot detection
        self._reboot_buf = b''
        self._needs_remount = False

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

        # Fast path: no escape byte
        if ESCAPE not in data:
            self._check_reboot(data)
            return data

        # Slow path: parse byte by byte
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
                if CMD_MIN <= cmd <= CMD_MAX:
                    # Valid VFS command — send ACK and dispatch
                    self._busy = True
                    try:
                        self._conn.write(_ESCAPE_BYTE)
                        self._handler.dispatch(cmd)
                    finally:
                        self._busy = False
                    i += 3
                else:
                    # Not a valid VFS command — pass through
                    out.append(data[i])
                    i += 1
            else:
                out.append(data[i])
                i += 1

        result = bytes(out) if out else None
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
        self._handler.close_all()
        self._conn.close()

    def hard_reset(self):
        self._conn.hard_reset()

    def reset_to_bootloader(self):
        self._conn.reset_to_bootloader()

    def reconnect(self, timeout=None):
        self._conn.reconnect(timeout)
