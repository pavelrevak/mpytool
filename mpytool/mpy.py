"""MicroPython tool: main MPY class"""

import mpytool.mpy_comm as _mpy_comm


class PathNotFound(_mpy_comm.MpyError):
    """File not found"""
    def __init__(self, file_name):
        self._file_name = file_name
        super().__init__(self.__str__())

    def __str__(self):
        return f"Path '{self._file_name}' was not found"


class FileNotFound(PathNotFound):
    """Folder not found"""
    def __str__(self):
        return f"File '{self._file_name}' was not found"


class DirNotFound(PathNotFound):
    """Folder not found"""
    def __str__(self):
        return f"Dir '{self._file_name}' was not found"


class Mpy():
    _CHUNK = 4096
    _ATTR_DIR = 0x4000
    _ATTR_FILE = 0x8000
    _HELPERS = {
        'stat': f"""
def _mpytool_stat(path):
    try:
        res = os.stat(path)
        if res[0] == {_ATTR_DIR}:
            return -1
        if res[0] == {_ATTR_FILE}:
            return res[6]
    except:
        return None
    return None
""",
        'tree': f"""
def _mpytool_tree(path):
    res_dir = []
    res_file = []
    dir_size = 0
    for entry in os.ilistdir(path):
        name, attr = entry[:2]
        if attr == {_ATTR_FILE}:
            size = entry[3]
            res_file.append((name, size, None))
            dir_size += size
        elif attr == {_ATTR_DIR}:
            if path in ('', '/'):
                sub_path = path + name
            else:
                sub_path = path + '/' + name
            _sub_path, sub_dir_size, sub_tree = _mpytool_tree(sub_path)
            res_dir.append((name, sub_dir_size, sub_tree))
            dir_size += sub_dir_size
    return path, dir_size, res_dir + res_file
""",
        'mkdir': f"""
def _mpytool_mkdir(path):
    path = path.rstrip('/')
    check_path = ''
    found = True
    for dir_part in path.split('/'):
        if check_path:
            check_path += '/'
        check_path += dir_part
        if found:
            try:
                result = os.stat(check_path)
                if result[0] == {_ATTR_FILE}:
                    return True
                continue
            except:
                found = False
        os.mkdir(check_path)
    return False
""",
        'rmdir': f"""
def _mpytool_rmdir(path):
    for name, attr, _inode, _size in os.ilistdir(path):
        if attr == {_ATTR_FILE}:
            os.remove(path + '/' + name)
        elif attr == {_ATTR_DIR}:
            _mpytool_rmdir(path + '/' + name)
    os.rmdir(path)
"""}

    def __init__(self, conn, log=None):
        self._conn = conn
        self._log = log
        self._mpy_comm = _mpy_comm.MpyComm(conn, log=log)
        self._imported = []
        self._load_helpers = []

    @property
    def conn(self):
        """access to connector instance
        """
        return self._conn

    @property
    def comm(self):
        """access to MPY communication instance
        """
        return self._mpy_comm

    def load_helper(self, helper):
        """Load helper function to MicroPython

        Arguments:
            helper: helper function name
        """
        if helper not in self._load_helpers:
            if helper not in self._HELPERS:
                raise _mpy_comm.MpyError(f'Helper {helper} not defined')
            self._mpy_comm.exec(self._HELPERS[helper])
            self._load_helpers.append(helper)

    def import_module(self, module):
        """Import module to MicroPython

        Arguments:
            module: module name to import
        """
        if module not in self._imported:
            self._mpy_comm.exec(f'import {module}')
            self._imported.append(module)

    def stat(self, path):
        """Stat path

        Arguments:
            path: path to stat

        Returns:
            None: if path not exists
            -1: on folder
            >= 0: on file and it's size
        """
        self.import_module('os')
        self.load_helper('stat')
        return self._mpy_comm.exec_eval(f"_mpytool_stat('{path}')")

    def ls(self, path=None):
        """List files on path

        Arguments:
            path: path to list, default: current path

        Returns:
            list of tuples, where each tuple for file:
                ('file_name', size)
            for directory:
                ('dir_name', None)
        """
        self.import_module('os')
        if path is None:
            path = ''
        try:
            result = self._mpy_comm.exec_eval(
                f"tuple(os.ilistdir('{path}'))")
            res_dir = []
            res_file = []
            for entry in result:
                name, attr = entry[:2]
                if attr == self._ATTR_DIR:
                    res_dir.append((name, None))
                elif attr == self._ATTR_FILE:
                    size = entry[3]
                    res_file.append((name, size))
        except _mpy_comm.CmdError as err:
            raise DirNotFound(path) from err
        return res_dir + res_file

    def tree(self, path=None):
        """Tree of directory structure with sizes

        Arguments:
            path: path to list, default: current path

        Returns: entry of directory or file
            for directory:
                (dir_path, size, [list of sub-entries])
            for empty directory:
                (dir_path, size, [])
            for file:
                (file_path, size, None)
        """
        self.import_module('os')
        self.load_helper('tree')
        if path is None:
            path = ''
        if path in ('', '.', '/'):
            return self._mpy_comm.exec_eval(f"_mpytool_tree('{path}')")
        # check if path exists
        result = self.stat(path)
        if result is None:
            raise DirNotFound(path)
        if result == -1:
            return self._mpy_comm.exec_eval(f"_mpytool_tree('{path}')")
        return((path, result[6], None))

    def mkdir(self, path):
        """make directory (also create all parents)

        Arguments:
            path: new directory path
        """
        self.import_module('os')
        self.load_helper('mkdir')
        if self._mpy_comm.exec_eval(f"_mpytool_mkdir('{path}')"):
            raise _mpy_comm.MpyError(f'Error creating directory, this is file: {path}')

    def delete(self, path):
        """delete file or directory (recursively)

        Arguments:
            path: path to delete
        """
        result = self.stat(path)
        if result is None:
            raise PathNotFound(path)
        if result == -1:
            self.import_module('os')
            self.load_helper('rmdir')
            self._mpy_comm.exec(f"_mpytool_rmdir('{path}')")
        else:
            self._mpy_comm.exec(f"os.remove('{path}')")

    def get(self, path):
        """Read file

        Arguments:
            path: file path to read

        Returns:
            bytes with file content
        """
        try:
            self._mpy_comm.exec(f"f = open('{path}', 'rb')")
        except _mpy_comm.CmdError as err:
            raise FileNotFound(path) from err
        data = b''
        while True:
            result = self._mpy_comm.exec_eval(f"f.read({self._CHUNK})")
            if not result:
                break
            data += result
        self._mpy_comm.exec("f.close()")
        return data

    def put(self, data, path):
        """Read file

        Arguments:
            data: bytes with file content
            path: file path to write
        """
        self._mpy_comm.exec(f"f = open('{path}', 'wb')")
        while data:
            chunk = data[:self._CHUNK]
            count = self._mpy_comm.exec_eval(f"f.write({chunk})", timeout=10)
            data = data[count:]
        self._mpy_comm.exec("f.close()")
