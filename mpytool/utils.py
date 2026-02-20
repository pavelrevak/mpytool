"""Utility functions for mpytool"""

import glob as _glob
import sys as _sys

from serial.tools.list_ports import comports as _comports


def is_remote_path(path: str) -> bool:
    """Check if path is remote (starts with :)"""
    return path.startswith(":")


def parse_remote_path(path: str) -> str:
    """Parse remote path, strip leading :

    Args:
        path: path with : prefix

    Returns:
        path without : prefix (empty string for ':' means CWD)
    """
    if not is_remote_path(path):
        raise ValueError(f"Not a remote path: {path}")
    return path[1:]


def split_commands(args: list[str], separator: str = "--") -> list[list[str]]:
    """Split command arguments by separator

    Args:
        args: list of arguments
        separator: separator string (default: --)

    Returns:
        list of command groups

    Example:
        split_commands(["cp", "a", ":/", "--", "reset"])
        => [["cp", "a", ":/"], ["reset"]]
    """
    if separator not in args:
        return [args] if args else []

    groups = []
    current = []
    for arg in args:
        if arg == separator:
            if current:
                groups.append(current)
                current = []
        else:
            current.append(arg)
    if current:
        groups.append(current)
    return groups


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


def detect_serial_ports() -> list[str]:
    """Detect available serial ports for MicroPython devices

    Returns:
        list of port paths sorted by likelihood of being MicroPython device
    """
    patterns = []
    if _sys.platform == "darwin":
        # Use cu.* (call-up) instead of tty.* - doesn't wait for DCD signal
        patterns = [
            "/dev/cu.usbmodem*",
            "/dev/cu.usbserial*",
            "/dev/cu.usb*",
        ]
    elif _sys.platform == "linux":
        patterns = [
            "/dev/ttyACM*",
            "/dev/ttyUSB*",
        ]
    elif _sys.platform == "win32":
        return sorted(
            p.device for p in _comports() if p.vid is not None)

    ports = []
    for pattern in patterns:
        ports.extend(_glob.glob(pattern))
    return sorted(set(ports))
