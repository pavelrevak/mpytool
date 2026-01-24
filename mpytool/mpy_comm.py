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

        Returns:
            True if we're in a known REPL state, False if recovery failed
        """
        if self._repl_mode is not None:
            return True
        if self._log:
            self._log.info('STOP CURRENT OPERATION')

        # Flush any pending data first
        self._conn.flush()

        # Try CTRL-C to interrupt any running program
        self._conn.write(b'\x03')
        try:
            self._conn.read_until(b'\r\n>>> ', timeout=1)
            self._repl_mode = False
            return True
        except _conn.Timeout:
            pass

        # CTRL-C didn't work, maybe we're in raw REPL mode
        # Try CTRL-B to exit raw REPL
        if self._log:
            self._log.debug("Trying CTRL-B to exit raw REPL")
        self._conn.write(b'\x02')
        try:
            self._conn.read_until(b'\r\n>>> ', timeout=1)
            self._repl_mode = False
            return True
        except _conn.Timeout:
            pass

        # Still not working, try CTRL-C again (device might have been mid-execution)
        self._conn.write(b'\x03')
        try:
            self._conn.read_until(b'\r\n>>> ', timeout=1)
            self._repl_mode = False
            return True
        except _conn.Timeout:
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
        # wait for prompt
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
