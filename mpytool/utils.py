"""Utility functions for mpytool"""

import sys


def is_remote_path(path: str) -> bool:
    """Check if path is remote (starts with :)"""
    return path.startswith(":")


def parse_remote_path(path: str) -> str:
    """Parse remote path, strip leading :

    Args:
        path: path with : prefix

    Returns:
        path without : prefix, or '/' if path is just ':'
    """
    if not is_remote_path(path):
        raise ValueError(f"Not a remote path: {path}")
    result = path[1:]
    return result if result else "/"


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


def detect_serial_ports() -> list[str]:
    """Detect available serial ports for MicroPython devices

    Returns:
        list of port paths sorted by likelihood of being MicroPython device
    """
    import glob

    patterns = []
    if sys.platform == "darwin":
        # Use cu.* (call-up) instead of tty.* - doesn't wait for DCD signal
        patterns = [
            "/dev/cu.usbmodem*",
            "/dev/cu.usbserial*",
            "/dev/cu.usb*",
        ]
    elif sys.platform == "linux":
        patterns = [
            "/dev/ttyACM*",
            "/dev/ttyUSB*",
        ]
    # Windows not yet supported

    ports = []
    for pattern in patterns:
        ports.extend(glob.glob(pattern))
    return sorted(set(ports))
