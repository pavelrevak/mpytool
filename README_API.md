# mpytool API Documentation

This document describes the Python API for mpytool library.

## Architecture

```
Layer 4: Mpy (mpy.py)             - High-level file operations API
Layer 3: MpyComm (mpy_comm.py)    - Raw REPL protocol handling
Layer 2: ConnSerial/ConnSocket    - Concrete connection implementations
Layer 1: Conn (conn.py)           - Abstract connection interface
```

## Quick Start

```python
import mpytool

# Connect via serial port
conn = mpytool.ConnSerial(port='/dev/ttyACM0', baudrate=115200)

# Create high-level API instance
mpy = mpytool.Mpy(conn)

# List files
files = mpy.ls()
print(files)
# [('lib', None), ('main.py', 1234), ('boot.py', 567)]

# Read file
data = mpy.get('main.py')
print(data.decode('utf-8'))

# Write file
mpy.put(b'print("Hello!")', 'hello.py')

# Create directory
mpy.mkdir('mydir/subdir')

# Delete file or directory
mpy.delete('old_file.py')
```

## Connection Classes

### ConnSerial

Serial port connection using pyserial.

```python
mpytool.ConnSerial(port, baudrate=115200, **serial_config)
```

**Parameters:**
- `port` (str): Serial port path (e.g., `/dev/ttyACM0`, `COM3`)
- `baudrate` (int): Baud rate, default 115200
- `**serial_config`: Additional pyserial configuration options

**Example:**
```python
conn = mpytool.ConnSerial(port='/dev/ttyACM0', baudrate=115200)
```

**Raises:**
- `ConnError`: If unable to open serial port

### ConnSocket

TCP socket connection for network-connected devices (e.g., WebREPL).

```python
mpytool.ConnSocket(address, log=None)
```

**Parameters:**
- `address` (str): Host address with optional port (e.g., `192.168.1.100` or `192.168.1.100:8266`)
- `log`: Optional logger instance

Default port is 23 if not specified.

**Example:**
```python
conn = mpytool.ConnSocket(address='192.168.1.100:8266')
```

**Raises:**
- `ConnError`: If unable to connect

## Mpy Class

High-level API for file operations on MicroPython devices.

```python
mpytool.Mpy(conn, log=None)
```

**Parameters:**
- `conn`: Connection instance (`ConnSerial` or `ConnSocket`)
- `log`: Optional logger instance

### Properties

#### conn
Access to the underlying connection instance.

```python
mpy.conn  # Returns ConnSerial or ConnSocket instance
```

#### comm
Access to the low-level MpyComm instance.

```python
mpy.comm  # Returns MpyComm instance
```

### Methods

#### ls(path=None)

List files and directories.

```python
mpy.ls(path=None)
```

**Parameters:**
- `path` (str, optional): Directory path to list, default is current directory

**Returns:**
- List of tuples: `[('name', size), ...]`
  - For files: `('filename', size_in_bytes)`
  - For directories: `('dirname', None)`

**Example:**
```python
>>> mpy.ls()
[('lib', None), ('main.py', 1234), ('boot.py', 567)]

>>> mpy.ls('lib')
[('utils.py', 890), ('config.py', 456)]
```

**Raises:**
- `DirNotFound`: If directory doesn't exist

#### tree(path=None)

Get recursive directory tree with sizes.

```python
mpy.tree(path=None)
```

**Parameters:**
- `path` (str, optional): Root path for tree, default is current directory

**Returns:**
- Tuple structure: `(path, total_size, children)`
  - For directory: `('dirname', size, [list of child entries])`
  - For file: `('filename', size, None)`

**Example:**
```python
>>> mpy.tree()
('.', 5678, [
    ('lib', 2000, [
        ('utils.py', 1000, None),
        ('config.py', 1000, None)
    ]),
    ('main.py', 1234, None)
])
```

**Raises:**
- `DirNotFound`: If directory doesn't exist

#### stat(path)

Get file/directory status.

```python
mpy.stat(path)
```

**Parameters:**
- `path` (str): Path to check

**Returns:**
- `None`: Path doesn't exist
- `-1`: Path is a directory
- `>= 0`: File size in bytes

**Example:**
```python
>>> mpy.stat('main.py')
1234

>>> mpy.stat('lib')
-1

>>> mpy.stat('nonexistent')
None
```

#### get(path, progress_callback=None)

Read file content from device.

```python
mpy.get(path, progress_callback=None)
```

**Parameters:**
- `path` (str): File path to read
- `progress_callback` (callable, optional): Callback function `(transferred, total)` for progress updates

**Returns:**
- `bytes`: File content

**Example:**
```python
>>> data = mpy.get('main.py')
>>> print(data.decode('utf-8'))
print("Hello World!")

# With progress callback
>>> def progress(transferred, total):
...     print(f"{transferred}/{total} bytes")
>>> data = mpy.get('large_file.bin', progress_callback=progress)
```

**Raises:**
- `FileNotFound`: If file doesn't exist

#### put(data, path, progress_callback=None)

Write file to device.

```python
mpy.put(data, path, progress_callback=None)
```

**Parameters:**
- `data` (bytes): File content to write
- `path` (str): Destination file path
- `progress_callback` (callable, optional): Callback function `(transferred, total)` for progress updates

**Example:**
```python
>>> mpy.put(b'print("Hello!")', 'hello.py')

# Upload local file
>>> with open('local_file.py', 'rb') as f:
...     mpy.put(f.read(), 'remote_file.py')

# With progress callback
>>> def progress(transferred, total):
...     percent = transferred * 100 // total
...     print(f"{percent}%")
>>> mpy.put(large_data, 'large_file.bin', progress_callback=progress)
```

#### mkdir(path)

Create directory (including parent directories).

```python
mpy.mkdir(path)
```

**Parameters:**
- `path` (str): Directory path to create

**Example:**
```python
>>> mpy.mkdir('lib/subdir/deep')  # Creates all parent directories
```

**Raises:**
- `MpyError`: If path exists as a file

#### delete(path)

Delete file or directory (recursively).

```python
mpy.delete(path)
```

**Parameters:**
- `path` (str): Path to delete

**Example:**
```python
>>> mpy.delete('old_file.py')      # Delete file
>>> mpy.delete('old_directory')    # Delete directory recursively
```

**Raises:**
- `PathNotFound`: If path doesn't exist

#### rename(src, dst)

Rename or move file/directory.

```python
mpy.rename(src, dst)
```

**Parameters:**
- `src` (str): Source path
- `dst` (str): Destination path

**Example:**
```python
>>> mpy.rename('old_name.py', 'new_name.py')
>>> mpy.rename('file.py', 'subdir/file.py')  # Move to subdirectory
```

#### hashfile(path)

Compute SHA256 hash of a file on device.

```python
mpy.hashfile(path)
```

**Parameters:**
- `path` (str): File path

**Returns:**
- `bytes`: SHA256 hash (32 bytes)
- `None`: If hashlib not available on device

**Example:**
```python
>>> hash_bytes = mpy.hashfile('main.py')
>>> print(hash_bytes.hex())
'a1b2c3d4...'
```

#### reset_state()

Reset internal state after device reset.

```python
mpy.reset_state()
```

Call this after `comm.soft_reset()` to clear cached helper/import state.

## MpyComm Class

Low-level REPL communication. Usually accessed via `mpy.comm`.

### Methods

#### exec(command, timeout=5)

Execute Python command on device.

```python
mpy.comm.exec(command, timeout=5)
```

**Parameters:**
- `command` (str): Python code to execute
- `timeout` (int): Maximum wait time in seconds

**Returns:**
- `bytes`: Command stdout output

**Example:**
```python
>>> mpy.comm.exec("print('Hello')")
b'Hello\r\n'

>>> mpy.comm.exec("import sys")
b''
```

**Raises:**
- `CmdError`: If command raises an exception on device

#### exec_eval(command, timeout=5)

Execute command and evaluate result.

```python
mpy.comm.exec_eval(command, timeout=5)
```

**Parameters:**
- `command` (str): Python expression to evaluate
- `timeout` (int): Maximum wait time in seconds

**Returns:**
- Evaluated Python object

**Example:**
```python
>>> mpy.comm.exec_eval("1 + 2")
3

>>> mpy.comm.exec_eval("list(range(5))")
[0, 1, 2, 3, 4]

>>> mpy.comm.exec_eval("{'a': 1, 'b': 2}")
{'a': 1, 'b': 2}
```

#### soft_reset()

Perform soft reset of the device.

```python
mpy.comm.soft_reset()
```

**Note:** Call `mpy.reset_state()` after this to clear cached state.

#### enter_raw_repl() / exit_raw_repl()

Manually enter/exit raw REPL mode. Usually handled automatically.

```python
mpy.comm.enter_raw_repl()
mpy.comm.exit_raw_repl()
```

## Exception Classes

### MpyError

Base exception for all mpytool errors.

```python
mpytool.MpyError
```

### ConnError

Connection-related errors.

```python
mpytool.ConnError
```

### Timeout

Timeout during communication.

```python
mpytool.Timeout
```

### CmdError

Command execution error on device.

```python
mpytool.CmdError
```

**Properties:**
- `cmd`: The command that failed
- `result`: Any output before error
- `error`: Error message from device

### PathNotFound / FileNotFound / DirNotFound

Path-related errors.

```python
mpytool.PathNotFound
mpytool.FileNotFound
mpytool.DirNotFound
```

## Complete Example

```python
import mpytool

# Connect to device
conn = mpytool.ConnSerial(port='/dev/ttyACM0', baudrate=115200)
mpy = mpytool.Mpy(conn)

try:
    # Show device info
    platform = mpy.comm.exec_eval("repr(__import__('sys').platform)")
    print(f"Platform: {platform}")

    # List root directory
    print("\nFiles:")
    for name, size in mpy.ls():
        if size is None:
            print(f"  {name}/")
        else:
            print(f"  {name} ({size} bytes)")

    # Upload a file
    code = b'''
def hello():
    print("Hello from MicroPython!")

hello()
'''
    mpy.put(code, 'hello.py')
    print("\nUploaded hello.py")

    # Execute the file
    result = mpy.comm.exec("exec(open('hello.py').read())")
    print(f"Output: {result.decode('utf-8')}")

    # Clean up
    mpy.delete('hello.py')
    print("Deleted hello.py")

except mpytool.MpyError as e:
    print(f"Error: {e}")

except mpytool.ConnError as e:
    print(f"Connection error: {e}")
```

## See Also

- [README.md](README.md) - CLI documentation and examples
- [GitHub Repository](https://github.com/pavelrevak/mpytool)
