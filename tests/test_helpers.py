"""Shared test helpers for integration and CLI tests"""


def reset_device(port):
    """Reset device before tests and wait for it to be ready

    Tries hardware reset first (USB-UART), falls back to soft reset (CDC).
    Actively waits for REPL to be ready (no fixed sleep).
    """
    from mpytool import ConnSerial, Mpy
    from mpytool.conn import ConnError

    conn = ConnSerial(port=port, baudrate=115200)
    mpy = Mpy(conn)
    try:
        # Try hardware reset first (USB-UART only)
        conn.hard_reset()
    except ConnError:
        # CDC devices don't support hardware reset, use soft reset
        mpy.soft_reset()
    # Wait for device to boot and REPL to be ready
    mpy.comm._repl_mode = None  # Force re-detection
    mpy.comm.stop_current_operation()
    conn.close()
