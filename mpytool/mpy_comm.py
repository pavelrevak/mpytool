"""MicroPython tool: MPY communication"""

import mpytool.conn as _conn


class MpyError(Exception):
    """General MPY error"""


class CmdError(MpyError):
    """Command execution error on device"""
    def __init__(self, cmd, result, error):
        self._cmd = cmd
        self._result = result
        self._error = error.decode('utf-8')
        super().__init__(self.__str__())

    def __str__(self):
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
                self._conn.write(b'\x02')  # Ctrl-B - exit raw REPL
            else:
                self._conn.write(b'\x03')  # Ctrl-C - interrupt program
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
        self._conn.write(b'\x01')
        self._conn.read_until(b'\r\n>')
        self._repl_mode = True

    def exit_raw_repl(self):
        if not self._repl_mode:
            return
        if self._log:
            self._log.info('EXIT RAW REPL')
        self._conn.write(b'\x02')
        self._conn.read_until(b'\r\n>>> ')
        self._repl_mode = False

    def soft_reset(self):
        self.stop_current_operation()
        self.exit_raw_repl()
        if self._log:
            self._log.info('SOFT RESET')
        self._conn.write(b'\x04')
        self._conn.read_until(b'soft reboot', timeout=1)
        self._repl_mode = None

    def soft_reset_raw(self):
        """Soft reset in raw REPL mode - clears RAM but doesn't run boot.py/main.py"""
        self.enter_raw_repl()
        if self._log:
            self._log.info('SOFT RESET (raw)')
        self._conn.write(b'\x04')
        self._conn.read_until(b'soft reboot', timeout=1)
        self._conn.read_until(b'>', timeout=1)
        self._repl_mode = True

    def exec(self, command, timeout=5):
        """Execute command

        Arguments:
            command: command to execute
            timeout: maximum waiting time for result

        Returns:
            command STDOUT result

        Raises:
            CmdError when command return error
        """
        self.enter_raw_repl()
        if self._log:
            self._log.info("CMD: %s", command)
        self._conn.write(bytes(command, 'utf-8'))
        self._conn.write(b'\x04')
        self._conn.read_until(b'OK', timeout)
        result = self._conn.read_until(b'\x04', timeout)
        if result:
            if self._log:
                self._log.info('RES: %s', bytes(result))
        err = self._conn.read_until(b'\x04>', timeout)
        if err:
            raise CmdError(command, result, err)
        return result

    def exec_eval(self, command, timeout=5):
        result = self.exec(f'print({command})', timeout)
        return eval(result)
