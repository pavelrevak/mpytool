"""Serial link speed test for MicroPython devices"""

import time as _time

from mpytool.mpy_comm import CTRL_D
from mpytool.utils import format_size

# MicroPython chat server (runs on device)
# Protocol (binary header: 2-byte little-endian size):
#   <size:2B> <data>  ->  .\n          upload data, store in buffer
#   <0x0000:2B>       ->  <buffer>\n   echo buffer back (download)
#   <0xFFFF:2B>       ->  (quit)
_CHAT_CODE = """\
import sys
_r=sys.stdin.buffer.read
_w=sys.stdout.buffer.write
buf=b''
while True:
 n=int.from_bytes(_r(2),'little')
 if n==65535:break
 if n==0:
  _w(buf)
  _w(b'\\n')
 else:
  buf=_r(n)
  _w(b'.\\n')
"""

_TEST_SIZES = [
    0, 16, 32, 64, 128, 256, 512,
    1024, 2048, 4096, 8192, 16384, 32768]
_REPEATS = 10


def _verify(sent, received):
    """Compare sent and received data, return error description or None"""
    if sent == received:
        return None
    if len(sent) != len(received):
        return f"length {len(received)}/{len(sent)}"
    for i, (a, b) in enumerate(zip(sent, received)):
        if a != b:
            return f"byte {i}: {a:#04x}!={b:#04x}"
    return None


def _fmt_speed(speed):
    if speed == 0:
        return '-'
    return format_size(speed) + '/s'


def speedtest(mpy_comm, log=None, pattern=0x55):
    """Run serial link speed test

    Starts a minimal chat server on the device and measures
    upload/download throughput at various data sizes.
    Each size is tested multiple times and averaged.
    Verifies data integrity via echo.

    Args:
        mpy_comm: MpyComm instance
        log: optional logger
        pattern: byte value for test data (default 0x55 = 'U')
    """
    conn = mpy_comm.conn

    # Start chat program on device via raw REPL
    mpy_comm.enter_raw_repl()
    conn.write(_CHAT_CODE.encode('utf-8'))
    conn.write(CTRL_D)
    conn.read_until(b'OK', timeout=5)

    print(f"  {'':>5}  {'upload':>12}  {'download':>12}  verify")

    try:
        for size in _TEST_SIZES:
            data = bytes([pattern]) * size
            up_total = 0
            down_total = 0
            errors = []

            size_hdr = size.to_bytes(2, 'little')
            echo_hdr = b'\x00\x00'

            for _ in range(_REPEATS):
                # Upload: send size header + data, measure until ACK
                t0 = _time.time()
                conn.write(size_hdr + data)
                conn.read_until(b'\n', timeout=30)
                up_total += _time.time() - t0

                # Download (echo): send zero header, measure until data arrives
                t0 = _time.time()
                conn.write(echo_hdr)
                echo = conn.read_until(b'\n', timeout=30)
                down_total += _time.time() - t0

                err = _verify(data, echo)
                if err:
                    errors.append(err)

            up_speed = size * _REPEATS / up_total if up_total > 0 else 0
            down_speed = size * _REPEATS / down_total if down_total > 0 else 0
            status = "ok" if not errors else errors[0]
            print(
                f"  {format_size(size):>5}"
                f"  {_fmt_speed(up_speed):>12}"
                f"  {_fmt_speed(down_speed):>12}"
                f"  {status}")

        # Quit chat server
        conn.write(b'\xff\xff')

    finally:
        # Clean up raw REPL state
        try:
            conn.read_until(CTRL_D, timeout=2)
            conn.read_until(CTRL_D + b'>', timeout=2)
        except Exception:
            pass
