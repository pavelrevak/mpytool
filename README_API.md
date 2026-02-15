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

**Additional methods:**

```python
conn.hard_reset()           # Hardware reset via RTS signal
conn.reset_to_bootloader()  # Enter bootloader via DTR/RTS (ESP32)
conn.reconnect(timeout=5)   # Reconnect after device reset (USB-CDC)
```

### ConnSocket

TCP socket connection for network-connected devices.

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

### Common Connection Methods

All connection types (including `ConnIntercept` used by mount) share these methods:

#### read(timeout=0)

Read available data from device (non-blocking by default).

```python
conn.read(timeout=0)
```

**Parameters:**
- `timeout` (float): How long to wait for data in seconds (0 = non-blocking)

**Returns:**
- `bytes`: Device output data
- `None`: No data available (timeout)

When mount is active, VFS requests from the device are handled transparently
inside `read()` — only non-protocol output (print, traceback, etc.) is returned.

**Example:**
```python
>>> data = conn.read(timeout=0.1)  # wait up to 100ms
>>> if data:
...     print(data.decode('utf-8', errors='replace'), end='')
```

#### busy

Property indicating if the connection is busy with an internal protocol exchange (VFS request).

```python
conn.busy  # True during VFS dispatch, False otherwise
```

Always `False` on plain `ConnSerial`/`ConnSocket`. On `ConnIntercept` (after mount), `True` while servicing a VFS request from the device.

#### fd

File descriptor for use with `select()`.

```python
conn.fd  # int file descriptor, or None
```

#### write(data)

Write data to device.

```python
conn.write(data)
```

**Raises:**
- `MpyError`: If connection is busy (VFS request in progress)

## Mpy Class

High-level API for file operations on MicroPython devices.

```python
mpytool.Mpy(conn, log=None, chunk_size=None)
```

**Parameters:**
- `conn`: Connection instance (`ConnSerial` or `ConnSocket`)
- `log`: Optional logger instance
- `chunk_size`: Transfer chunk size (512, 1024, 2048, 4096, 8192, 16384, 32768). Auto-detected from device RAM if not specified.

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

#### put(data, path, progress_callback=None, compress=None)

Write file to device.

```python
mpy.put(data, path, progress_callback=None, compress=None)
```

**Parameters:**
- `data` (bytes): File content to write
- `path` (str): Destination file path
- `progress_callback` (callable, optional): Callback function `(transferred, total)` for progress updates
- `compress` (bool, optional): Enable/disable compression. `None` = auto-detect based on device RAM and deflate availability

**Returns:**
- `tuple`: `(encodings_used, wire_bytes)` where `encodings_used` is a set of encoding types ('raw', 'base64', 'compressed') and `wire_bytes` is the number of bytes sent over the wire

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

#### getcwd()

Get current working directory on device.

```python
mpy.getcwd()
```

**Returns:**
- `str`: Current working directory path

**Example:**
```python
>>> mpy.getcwd()
'/'

>>> mpy.chdir('/lib')
>>> mpy.getcwd()
'/lib'
```

#### chdir(path)

Change current working directory on device.

```python
mpy.chdir(path)
```

**Parameters:**
- `path` (str): Directory path to change to (absolute or relative)

**Example:**
```python
>>> mpy.chdir('/lib')           # Absolute path
>>> mpy.chdir('subdir')         # Relative path
>>> mpy.chdir('..')             # Parent directory
>>> mpy.chdir('/')              # Root directory
```

**Raises:**
- `DirNotFound`: If directory doesn't exist or path is a file

#### get_sys_path()

Get current module search path from device.

```python
mpy.get_sys_path()
```

**Returns:**
- `list`: Current `sys.path` list from device

**Example:**
```python
>>> mpy.get_sys_path()
['', '/lib']
```

#### set_sys_path(*paths)

Replace entire module search path on device.

```python
mpy.set_sys_path(*paths)
```

**Parameters:**
- `*paths` (str): New paths for `sys.path`

**Example:**
```python
>>> mpy.set_sys_path('', '/lib')          # Set to ['', '/lib']
>>> mpy.set_sys_path('/', '/sd/lib')      # Set to ['/', '/sd/lib']
```

#### prepend_sys_path(*paths)

Add paths to beginning of module search path (automatically removes duplicates).

```python
mpy.prepend_sys_path(*paths)
```

**Parameters:**
- `*paths` (str): Paths to prepend to `sys.path`

If a path already exists in `sys.path`, it is removed first and then added
at the beginning. This effectively moves the path to the front.

**Example:**
```python
>>> mpy.get_sys_path()
['', '/lib']
>>> mpy.prepend_sys_path('/custom')
>>> mpy.get_sys_path()
['/custom', '', '/lib']

# Move existing path to front
>>> mpy.prepend_sys_path('/lib')
>>> mpy.get_sys_path()
['/lib', '/custom', '']
```

#### append_sys_path(*paths)

Add paths to end of module search path (automatically removes duplicates).

```python
mpy.append_sys_path(*paths)
```

**Parameters:**
- `*paths` (str): Paths to append to `sys.path`

If a path already exists in `sys.path`, it is removed first and then added
at the end. This effectively moves the path to the back.

**Example:**
```python
>>> mpy.get_sys_path()
['', '/lib']
>>> mpy.append_sys_path('/sdcard/lib')
>>> mpy.get_sys_path()
['', '/lib', '/sdcard/lib']

# Move existing path to end
>>> mpy.append_sys_path('')
>>> mpy.get_sys_path()
['/lib', '/sdcard/lib', '']
```

#### remove_from_sys_path(*paths)

Remove specified paths from module search path.

```python
mpy.remove_from_sys_path(*paths)
```

**Parameters:**
- `*paths` (str): Paths to remove from `sys.path`

Silently ignores paths that don't exist in `sys.path`.

**Example:**
```python
>>> mpy.get_sys_path()
['', '/lib', '/custom']
>>> mpy.remove_from_sys_path('/custom')
>>> mpy.get_sys_path()
['', '/lib']

# Remove multiple paths
>>> mpy.remove_from_sys_path('', '/lib')
>>> mpy.get_sys_path()
[]
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

#### fileinfo(files)

Get file info (size and hash) for multiple files in one call.

```python
mpy.fileinfo(files)
```

**Parameters:**
- `files` (dict): Dictionary `{path: expected_size}` - hash is only computed if sizes match

**Returns:**
- `dict`: `{path: (size, hash)}` - hash is `None` if sizes don't match
- `dict`: `{path: None}` - if file doesn't exist
- `None`: If hashlib not available on device

**Example:**
```python
>>> mpy.fileinfo({'/main.py': 1234, '/boot.py': 567})
{'/main.py': (1234, b'\xa1\xb2...'), '/boot.py': (567, b'\xc3\xd4...')}
```

#### reset_state()

Reset internal state after device reset.

```python
mpy.reset_state()
```

Call this after `comm.soft_reset()` to clear cached helper/import state.

### Device Information

#### platform()

Get device platform information.

```python
mpy.platform()
```

**Returns:**
- `dict`: `{'platform': 'esp32', 'version': '...', 'impl': 'micropython', 'machine': '...'}`

#### unique_id()

Get device unique ID.

```python
mpy.unique_id()
```

**Returns:**
- `str`: Hex-encoded unique ID (e.g., `'e660123456789abc'`)

#### memory()

Get memory usage.

```python
mpy.memory()
```

**Returns:**
- `dict`: `{'alloc': bytes_used, 'free': bytes_free, 'total': total_bytes}`

### Mount

#### mount(local_path, mount_point='/remote', log=None, mpy_cross=None)

Mount a local directory on device as readonly VFS.

```python
mpy.mount(local_path, mount_point='/remote', log=None, mpy_cross=None)
```

**Parameters:**
- `local_path` (str): Local directory to mount
- `mount_point` (str): Device mount point, default `/remote`
- `log`: Optional logger instance
- `mpy_cross`: Optional `MpyCross` instance for transparent `.mpy` compilation

**Returns:**
- `MountHandler` instance

The device can then read, import and execute files from the local directory
without uploading to flash. A MicroPython agent is injected into the device
that forwards filesystem requests (stat, listdir, open, read, close) to the
PC over the serial link. The connection is wrapped in a transparent proxy
(`ConnIntercept`) that intercepts VFS protocol messages while passing REPL
I/O through.

**Transparent .mpy compilation:** If `mpy_cross` is provided, `.py` files are
automatically compiled to `.mpy` bytecode on-demand when imported by the device.
Compiled files are cached in `__pycache__/` with mtime checking. Boot files
(`boot.py`, `main.py`) and empty files remain as `.py`. Falls back to `.py`
if compilation fails. Prebuilt `.mpy` files have priority over cache.

Mount does not change CWD or `sys.path` — use `mpy.chdir()` to set working
directory after mount. Multiple independent (non-nested) mounts are supported.

After calling `mount()`, use `mpy.comm.exit_raw_repl()` to enter friendly
REPL, or use the CLI `mount` command which handles this automatically.

Soft reset (Ctrl+D) triggers automatic re-mount.

**Example:**
```python
>>> handler = mpy.mount('./src')
>>> mpy.chdir('/remote')          # set CWD to mounted directory
>>> mpy.comm.exit_raw_repl()
# Device can now: import module  (from ./src/module.py)
#                 open('/remote/data.txt').read()

# With .mpy compilation:
>>> from mpytool.mpy_cross import MpyCross
>>> from mpytool.logger import SimpleColorLogger
>>> log = SimpleColorLogger()
>>> mpy_cross = MpyCross(log)
>>> mpy_cross.init(mpy.platform())
>>> handler = mpy.mount('./src', mpy_cross=mpy_cross)
# Device imports .mpy files (compiled on-demand)
```

**Raises:**
- `MpyError`: If agent injection or mount fails, or if mount point is nested
  inside an existing mount

#### add_submount(mount_point, subpath, local_path)

Add a virtual submount (symlink) into an existing mount.

```python
mpy.add_submount(mount_point, subpath, local_path)
```

**Parameters:**
- `mount_point` (str): Existing device mount point (e.g., `/remote`)
- `subpath` (str): Path relative to mount root (e.g., `lib/pkg`)
- `local_path` (str): Local file or directory path

Links a local file or directory into the mounted VFS at the specified subpath.
The device sees it as part of the mounted filesystem. Virtual intermediate
directories are created automatically (e.g., adding `lib/pkg` creates a virtual
`lib` directory).

Works entirely on the PC side — no changes needed on the device agent.
This is the API equivalent of the CLI `ln` command.

**Example:**
```python
>>> mpy.mount('./app', '/remote')
>>> mpy.add_submount('/remote', 'lib/drivers', './drivers')
>>> mpy.add_submount('/remote', 'lib/config.py', './config.py')
>>> mpy.comm.exit_raw_repl()
# Device can now:
#   import drivers.motor   (from ./drivers/motor.py)
#   open('/remote/lib/config.py').read()
```

**Raises:**
- `MpyError`: If no mount exists at `mount_point`

#### stop()

Stop running program on device and return to REPL prompt.

```python
mpy.stop()
```

Sends Ctrl+C to interrupt the running program and waits for the `>>> ` prompt.
After `stop()`, API methods like `exec()` work normally. VFS remains mounted.

**Example:**
```python
>>> mpy.mount('./src')
>>> mpy.comm.exec("exec(open('/remote/main.py').read())", timeout=0)
>>> # ... device runs main.py ...
>>> mpy.stop()          # Ctrl+C, wait for >>>
>>> mpy.comm.exec_eval("1 + 1")  # works
2
```

#### Mount with monitoring (select loop)

After mount, use `conn.read()` in a loop to service VFS requests and capture
device output. This is the API equivalent of the CLI `mount` + `monitor` command.

```python
import select
import sys
import mpytool

conn = mpytool.ConnSerial(port='/dev/ttyACM0')
mpy = mpytool.Mpy(conn)

# Mount and run script
mpy.mount('./src')
mpy.comm.exec("exec(open('/remote/main.py').read())", timeout=0)

# Monitor output with select loop
try:
    while True:
        ready, _, _ = select.select([conn.fd], [], [], 1.0)
        if ready:
            data = conn.read()
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
except KeyboardInterrupt:
    mpy.stop()
    conn.close()
```

For simple use cases without `select()`:

```python
mpy.mount('./src')
mpy.comm.exec("exec(open('/remote/main.py').read())", timeout=0)

try:
    while True:
        data = conn.read(timeout=0.1)
        if data:
            print(data.decode('utf-8', errors='replace'), end='')
except KeyboardInterrupt:
    mpy.stop()
    conn.close()
```

### Stop and Reset Methods

#### stop()

Stop running program and return to REPL prompt (sends Ctrl+C).

```python
mpy.stop()
```

See [Mount section](#stop) for detailed usage with mount.

#### soft_reset()

Soft reset device (runs boot.py/main.py).

```python
mpy.soft_reset()
```

#### soft_reset_raw()

Soft reset in raw REPL mode (clears RAM only, doesn't run boot.py/main.py).

```python
mpy.soft_reset_raw()
```

#### machine_reset(reconnect=True)

MCU reset using `machine.reset()`.

```python
mpy.machine_reset(reconnect=True)
```

**Parameters:**
- `reconnect` (bool): If True, attempt to reconnect after reset (for USB-CDC ports)

#### hard_reset()

Hardware reset using RTS signal (serial only).

```python
mpy.hard_reset()
```

**Raises:**
- `NotImplementedError`: If connection doesn't support hardware reset

### Flash/Partition Methods

#### partitions()

Get partition table information (ESP32 only).

```python
mpy.partitions()
```

**Returns:**
- `dict` with keys:
  - `'partitions'`: List of partition dicts with keys: `label`, `type`, `type_name`, `subtype`, `subtype_name`, `offset`, `size`, `encrypted`, `running`, `filesystem`, `fs_block_size`
  - `'boot'`: Boot partition label or None
  - `'next_ota'`: Next OTA partition label or None
  - `'next_ota_size'`: Next OTA partition size or None

**Example:**
```python
>>> info = mpy.partitions()
>>> for p in info['partitions']:
...     print(f"{p['label']}: {p['size']} bytes")
factory: 2031616 bytes
nvs: 24576 bytes
vfs: 2097152 bytes
```

#### flash_info()

Get flash information (RP2 only).

```python
mpy.flash_info()
```

**Returns:**
- `dict`: `{'size': total_bytes, 'block_size': block_size, 'block_count': count, 'filesystem': type, 'fs_block_size': size}`

#### flash_read(label=None, progress_callback=None)

Read flash content (RP2) or partition content (ESP32).

```python
mpy.flash_read(label=None, progress_callback=None)
```

**Parameters:**
- `label` (str, optional): Partition label for ESP32. If None, reads entire RP2 user flash.
- `progress_callback` (callable, optional): Callback `(transferred, total)` for progress

**Returns:**
- `bytes`: Flash/partition content

**Example:**
```python
# RP2 - read entire user flash
>>> data = mpy.flash_read()

# ESP32 - read partition by label
>>> data = mpy.flash_read(label='factory')
>>> with open('backup.bin', 'wb') as f:
...     f.write(data)
```

**Raises:**
- `MpyError`: If wrong platform (label requires ESP32, no label requires RP2)

#### flash_write(data, label=None, progress_callback=None, compress=None)

Write data to flash (RP2) or partition (ESP32).

```python
mpy.flash_write(data, label=None, progress_callback=None, compress=None)
```

**Parameters:**
- `data` (bytes): Data to write
- `label` (str, optional): Partition label for ESP32. If None, writes to RP2 user flash.
- `progress_callback` (callable, optional): Callback `(transferred, total)` or `(transferred, total, wire_bytes)` for ESP32
- `compress` (bool, optional): Enable/disable compression (None = auto-detect, ESP32 only)

**Returns:**
- `dict`: `{'size': total_size, 'written': bytes_written}` for RP2
- `dict`: `{'size': total_size, 'written': bytes_written, 'wire_bytes': bytes_sent, 'compressed': bool}` for ESP32

**Example:**
```python
# RP2 - write to user flash
>>> mpy.flash_write(data)

# ESP32 - write to partition
>>> with open('nvs_backup.bin', 'rb') as f:
...     data = f.read()
>>> mpy.flash_write(data, label='nvs')
```

**Raises:**
- `MpyError`: If wrong platform or data too large

#### flash_erase(label=None, full=False, progress_callback=None)

Erase flash (RP2) or partition (ESP32).

```python
mpy.flash_erase(label=None, full=False, progress_callback=None)
```

**Parameters:**
- `label` (str, optional): Partition label for ESP32. If None, erases RP2 user flash.
- `full` (bool): If True, erase entire flash/partition. If False, erase only first 2 blocks (filesystem reset).
- `progress_callback` (callable, optional): Callback `(transferred, total)` for progress

**Returns:**
- `dict`: `{'erased': bytes_erased}` for RP2
- `dict`: `{'erased': bytes_erased, 'label': label}` for ESP32

**Example:**
```python
# RP2 - quick erase (filesystem reset)
>>> mpy.flash_erase()

# ESP32 - full partition erase
>>> mpy.flash_erase(label='nvs', full=True)
```

#### ota_write(data, progress_callback=None, compress=None)

Write firmware to next OTA partition and set it as boot partition.

```python
mpy.ota_write(data, progress_callback=None, compress=None)
```

**Parameters:**
- `data` (bytes): Firmware content (.app-bin file)
- `progress_callback` (callable, optional): Callback `(transferred, total, wire_bytes)`
- `compress` (bool, optional): Enable/disable compression

**Returns:**
- `dict`: `{'target': label, 'offset': partition_offset, 'size': fw_size, 'wire_bytes': bytes_sent, 'compressed': bool}`

**Raises:**
- `MpyError`: If OTA not available or firmware too large

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
- `timeout` (int): Maximum wait time in seconds. `0` = submit only (send code, don't wait for output)

**Returns:**
- `bytes`: Command stdout output (`b''` when timeout=0)

**Example:**
```python
>>> mpy.comm.exec("print('Hello')")
b'Hello\r\n'

>>> mpy.comm.exec("import sys")
b''

# Submit code without waiting for output (fire-and-forget)
>>> mpy.comm.exec("while True: print('tick')", timeout=0)
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

Perform soft reset of the device (exits raw REPL, runs boot.py/main.py).

```python
mpy.comm.soft_reset()
```

**Note:** Call `mpy.reset_state()` after this to clear cached state.

#### soft_reset_raw()

Perform soft reset in raw REPL mode (clears RAM but doesn't run boot.py/main.py).

```python
mpy.comm.soft_reset_raw()
```

**Note:** Call `mpy.reset_state()` after this to clear cached state. Useful for freeing memory before file operations.

#### enter_raw_repl() / exit_raw_repl()

Manually enter/exit raw REPL mode. Usually handled automatically.

```python
mpy.comm.enter_raw_repl()
mpy.comm.exit_raw_repl()
```

#### exec_raw_paste(command, timeout=5)

Execute Python command using raw-paste mode with flow control.

Raw-paste mode compiles code as it receives it, using less RAM and providing
better reliability for large code transfers. Requires MicroPython 1.17+.

```python
mpy.comm.exec_raw_paste(command, timeout=5)
```

**Parameters:**
- `command` (str or bytes): Python code to execute
- `timeout` (int): Maximum wait time in seconds. `0` = submit only (send code, don't wait for output)

**Returns:**
- `bytes`: Command stdout output (`b''` when timeout=0)

**Example:**
```python
>>> mpy.comm.exec_raw_paste("print('Hello')")
b'Hello\r\n'

# Large code that exceeds window size
>>> large_code = '\n'.join(f'x{i} = {i}' for i in range(100))
>>> mpy.comm.exec_raw_paste(large_code + '\nprint(x99)')
b'99\r\n'
```

**Raises:**
- `MpyError`: If raw-paste mode is not supported by device
- `CmdError`: If command raises an exception on device

#### try_raw_paste(command, timeout=5)

Try raw-paste mode, fall back to regular exec if not supported.

```python
mpy.comm.try_raw_paste(command, timeout=5)
```

**Parameters:**
- `command` (str or bytes): Python code to execute
- `timeout` (int): Maximum wait time in seconds. `0` = submit only (send code, don't wait for output)

**Returns:**
- `bytes`: Command stdout output (`b''` when timeout=0)

**Example:**
```python
# Works on any MicroPython version
>>> result = mpy.comm.try_raw_paste("print(1+1)")
b'2\r\n'
```

This method automatically detects if raw-paste mode is supported and caches
the result. On older MicroPython versions, it silently falls back to regular
`exec()`.

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
