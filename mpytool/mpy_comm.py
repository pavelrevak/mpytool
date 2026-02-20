"""MicroPython tool: MPY communication"""

import re as _re
import time as _time

import mpytool.conn as _conn

# REPL control characters
CTRL_A = b'\x01'  # Enter raw REPL
CTRL_B = b'\x02'  # Exit raw REPL
CTRL_C = b'\x03'  # Interrupt
CTRL_D = b'\x04'  # Execute / Soft reset / End raw-paste
CTRL_E = b'\x05'  # Paste mode

# Raw-paste mode
RAW_PASTE_ENTER = CTRL_E + b'A' + CTRL_A  # Enter raw-paste mode sequence
RAW_PASTE_ACK = b'\x01'  # Flow control ACK


class MpyError(Exception):
    """General MPY error"""


class CmdError(MpyError):
    """Command execution error on device"""

    # Known MicroPython OSError codes
    _OSERROR_MESSAGES = {
        '2': 'No such file or directory',
        '13': 'Permission denied',
        '17': 'File exists',
        '19': 'No such device',
        '21': 'Is a directory',
        '22': 'Invalid argument',
        '28': 'No space left on device',
        '30': 'Read-only filesystem',
        '110': 'Connection timed out',
        '113': 'No route to host',
    }

    def __init__(self, cmd, result, error):
        self._cmd = cmd
        self._result = result
        self._error = error.decode('utf-8')
        super().__init__(self.__str__())

    def _friendly_error(self):
        """Translate known OSError codes to human-readable messages"""
        match = _re.search(r'OSError: (\d+)', self._error)
        if match:
            code = match.group(1)
            msg = self._OSERROR_MESSAGES.get(code)
            if msg:
                return f'OSError: {msg} (errno {code})'
        return None

    def __str__(self):
        friendly = self._friendly_error()
        if friendly:
            return friendly
        res = f'Command:\n  {self._cmd}\n'
        if self._result:
            res += f'Result:\n  {self._result}\n'
        if self._error:
            res += f'Error:\n  {self._error}'
        return res

    @property
    def cmd(self):
        return self._cmd

    @property
    def result(self):
        return self._result

    @property
    def error(self):
        return self._error


class MpyComm():
    def __init__(self, conn, log=None):
        self._conn = conn
        self._log = log
        self._repl_mode = None
        self._raw_paste_supported = None  # None = unknown, True/False = detected

    @property
    def conn(self):
        return self._conn

    def stop_current_operation(self):
        """Stop any running operation and get to known REPL state.

        Some USB/serial converters reset the device when port opens.
        We send multiple Ctrl-C/Ctrl-B with short timeouts to catch the device
        after reset completes and program starts running.

        Returns:
            True if we're in a known REPL state, False if recovery failed
        """
        if self._repl_mode is not None:
            return True
        if self._log:
            self._log.info('STOP CURRENT OPERATION')
        self._conn.flush()

        # Try multiple attempts with short timeouts
        # Interleave Ctrl-C (interrupt program) and Ctrl-B (exit raw REPL)
        # This handles USB/serial converters that reset device on port open
        for attempt in range(15):
            # Alternate: Ctrl-C, Ctrl-C, Ctrl-B, repeat
            if attempt % 3 == 2:
                self._conn.write(CTRL_B)
            else:
                self._conn.write(CTRL_C)
            # Stale VFS agent recovery: send VFS ACK byte (0x18) to
            # unblock agent stuck in _mt_bg() waiting for response.
            # The fake ACK causes the agent to proceed, read garbage
            # parameters, crash with exception, and return to REPL.
            if attempt >= 4:
                self._conn.write(b'\x18')
            try:
                self._conn.read_until(b'\r\n>>> ', timeout=0.2)
                self._repl_mode = False
                return True
            except _conn.Timeout:
                pass

        if self._log:
            self._log.warning("Could not establish REPL state")
        return False

    def enter_raw_repl(self, max_retries=3):
        if self._repl_mode is True:
            return
        retries = 0
        while not self.stop_current_operation():
            retries += 1
            if retries >= max_retries:
                raise MpyError("Could not establish REPL connection")
            if self._log:
                self._log.warning('..retry %d/%d', retries, max_retries)
        if self._log:
            self._log.info('ENTER RAW REPL')
        self._conn.write(CTRL_A)
        self._conn.read_until(b'\r\n>')
        self._repl_mode = True

    def exit_raw_repl(self):
        if not self._repl_mode:
            return
        if self._log:
            self._log.info('EXIT RAW REPL')
        self._conn.write(CTRL_B)
        self._conn.read_until(b'\r\n>>> ')
        self._repl_mode = False

    def soft_reset(self):
        self.stop_current_operation()
        self.exit_raw_repl()
        if self._log:
            self._log.info('SOFT RESET')
        self._conn.write(CTRL_D)
        self._conn.read_until(b'soft reboot', timeout=1)
        self._repl_mode = None
        self._raw_paste_supported = None

    def soft_reset_raw(self):
        """Soft reset in raw REPL mode - clears RAM but doesn't run boot.py/main.py"""
        self.enter_raw_repl()
        if self._log:
            self._log.info('SOFT RESET (raw)')
        self._conn.write(CTRL_D)
        self._conn.read_until(b'soft reboot', timeout=1)
        self._conn.read_until(b'>', timeout=1)
        self._repl_mode = True
        self._raw_paste_supported = None

    def reset_state(self):
        """Reset internal state (call after device reset)"""
        self._repl_mode = None
        self._raw_paste_supported = None

    def exec(self, command, timeout=5):
        """Execute command

        Arguments:
            command: command to execute
            timeout: maximum waiting time for result,
                0 = submit only (send code, don't wait for output)

        Returns:
            command STDOUT result

        Raises:
            CmdError when command return error
        """
        send_timeout = 5 if timeout == 0 else timeout
        self.enter_raw_repl()
        if self._log:
            self._log.info("CMD: %s", command)
        self._conn.write(bytes(command, 'utf-8'))
        self._conn.write(CTRL_D)
        self._conn.read_until(b'OK', send_timeout)
        if timeout == 0:
            self._repl_mode = False
            return b''
        result = self._conn.read_until(CTRL_D, timeout)
        if result:
            if self._log:
                self._log.info('RES: %s', bytes(result))
        err = self._conn.read_until(CTRL_D + b'>', timeout)
        if err:
            raise CmdError(command, result, err)
        return result

    def exec_eval(self, command, timeout=5):
        result = self.exec(f'print({command})', timeout)
        return eval(result)

    def _scan_for_rawpaste_header(self, first_byte, second_byte, timeout):
        """Scan buffer for raw-paste header pattern (for slow UART devices).

        Returns:
            tuple: (header, status) bytes
        Raises:
            MpyError if pattern not found
        """
        if self._log:
            self._log.warning(
                "Raw-paste header mismatch (got %d) - scanning", first_byte)

        scanned = bytearray([first_byte, second_byte])
        max_scan = 50

        while len(scanned) < max_scan:
            # Look for R\x01 or R\x00 pattern
            for pattern in [b'R\x01', b'R\x00']:
                idx = scanned.find(pattern)
                if idx != -1:
                    if self._log:
                        self._log.info(
                            "Found header at offset %d (discarded %d garbage bytes)",
                            idx, idx)
                    return ord('R'), scanned[idx + 1]

            try:
                scanned.extend(self._conn.read_bytes(1, timeout=0.2))
            except _conn.Timeout:
                break

        raise MpyError(
            f"Raw-paste header not found in {len(scanned)} bytes: "
            f"{bytes(scanned[:20])!r}...")

    def _read_rawpaste_window_size(self, timeout):
        """Read raw-paste window size from device."""
        window_bytes = self._conn.read_bytes(2, timeout)
        window_size = int.from_bytes(window_bytes, 'little')
        if self._log:
            self._log.info("Raw-paste window size: %d", window_size)
        return window_size

    def _send_data_with_flow_control(self, data, window_size, timeout):
        """Send data with flow control (returns early if device aborts)."""
        remaining_window = window_size
        offset = 0

        while offset < len(data):
            if remaining_window == 0 or self._conn._has_data(0):
                flow_byte = self._conn.read_bytes(1, timeout)
                if flow_byte == RAW_PASTE_ACK:
                    remaining_window += window_size
                elif flow_byte == CTRL_D:
                    self._conn.write(CTRL_D)
                    return  # Device aborted (syntax error during compilation)

            if remaining_window > 0:
                chunk_size = min(remaining_window, len(data) - offset)
                self._conn.write(data[offset:offset + chunk_size])
                offset += chunk_size
                remaining_window -= chunk_size

        self._conn.write(CTRL_D)

    def _wait_for_paste_complete(self, timeout):
        """Consume remaining ACKs and wait for CTRL_D echo."""
        while True:
            byte = self._conn.read_bytes(1, timeout)
            if byte == CTRL_D:
                break

    def _read_execution_result(self, command, timeout):
        """Read and parse execution result."""
        result = self._conn.read_until(CTRL_D, timeout)
        if result and self._log:
            self._log.info('RES: %s', bytes(result))
        err = self._conn.read_until(CTRL_D + b'>', timeout)
        if err:
            raise CmdError(command.decode('utf-8', errors='replace'), result, err)
        return result

    def exec_raw_paste(self, command, timeout=5):
        """Execute code via raw-paste mode (flow-controlled, less RAM).

        Arguments:
            command: code to execute (str or bytes)
            timeout: max wait time (0 = submit only, don't wait for output)

        Returns:
            command STDOUT result

        Raises:
            CmdError: command execution error
            MpyError: raw-paste not supported
        """
        send_timeout = 5 if timeout == 0 else timeout
        self.enter_raw_repl()

        if isinstance(command, str):
            command = command.encode('utf-8')

        if self._log:
            self._log.info("CMD (raw-paste, %d bytes)", len(command))

        self._conn.write(RAW_PASTE_ENTER)

        header, status = self._conn.read_bytes(2, send_timeout)

        # Scan for pattern if garbage in buffer (slow UART)
        if header != ord('R'):
            header, status = self._scan_for_rawpaste_header(
                header, status, send_timeout)

        if status == 0:
            self._raw_paste_supported = False
            raise MpyError("Raw-paste mode not supported by device")

        if status != 1:
            raise MpyError(f"Unexpected raw-paste status: {status}")

        self._raw_paste_supported = True

        window_size = self._read_rawpaste_window_size(send_timeout)
        self._send_data_with_flow_control(command, window_size, send_timeout)
        self._wait_for_paste_complete(send_timeout)

        if timeout == 0:
            self._repl_mode = False
            return b''

        return self._read_execution_result(command, timeout)

    def try_raw_paste(self, command, timeout=5):
        """Try raw-paste mode, fall back to regular exec if not supported.

        Arguments:
            command: command to execute
            timeout: maximum waiting time for result

        Returns:
            command STDOUT result
        """
        # If we know raw-paste is not supported, skip it
        if self._raw_paste_supported is False:
            return self.exec(command, timeout)

        try:
            return self.exec_raw_paste(command, timeout)
        except MpyError as e:
            if "not supported" in str(e):
                if self._log:
                    self._log.info("Raw-paste not supported, using regular exec")
                return self.exec(command, timeout)
            raise
