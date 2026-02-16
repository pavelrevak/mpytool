# mpytool vs mpremote Comparison

Detailed comparison between [mpytool](https://github.com/pavelrevak/mpytool) and [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html).

## Commands

| Command | mpytool | mpremote |
|---------|---------|----------|
| List files | `ls :/path` | `fs ls path` or `ls path` |
| Tree view | `tree :/path` | `fs tree path` or `tree path` |
| Print file | `cat :file` | `fs cat file` or `cat file` |
| Copy files | `cp src :dst` | `fs cp src :dst` or `cp src :dst` |
| Delete file | `rm :file` | `fs rm file` or `rm file` |
| Delete dir | `rm :dir` | `fs rmdir dir` or `rmdir dir` |
| Create dir | `mkdir :dir` | `fs mkdir dir` or `mkdir dir` |
| Move/rename | `mv :src :dst` | - |
| Touch file | - | `fs touch file` or `touch file` |
| SHA256 hash | - | `fs sha256sum file` or `sha256sum file` |
| Execute code | `exec "code"` | `exec "code"` |
| Evaluate expr | - | `eval "expr"` |
| Run script | `run script.py` | `run script.py` |
| Enter REPL | `repl` | `repl` |
| Monitor output | `monitor` | - (use `repl`) |
| Soft reset | `reset` | `soft-reset` |
| Machine reset | `reset --machine` | `reset` |
| Hardware reset | `reset --rts` | - |
| Bootloader | `reset --boot` | `bootloader` |
| Device info | `info` | - (use `exec`) |
| Disk usage | - | `df` |
| Speed test | `speedtest` | - |
| Mount VFS | `mount ./dir :/mp` | `mount ./dir` |
| Unmount VFS | - (exit session) | `umount` |
| Virtual submount | `ln ./src :/dst` | - |
| Package install | - | `mip install pkg` |
| RTC control | - | `rtc`, `rtc --set` |
| ROMFS manage | - | `romfs query/build/deploy` |
| Edit remote file | - | `edit :file` |
| Flash read/write | `flash read/write/erase` | - |
| OTA update | `ota firmware.bin` | - |
| Print CWD | `pwd` | - (use `exec`) |
| Change CWD | `cd :path` | - |
| Manage sys.path | `path` | - |
| Sleep | `sleep 2` (seconds) | `sleep 2000` (milliseconds) |
| Connect device | Auto / `-p port` | `connect dev` |
| Disconnect | - | `disconnect` |
| Resume session | - | `resume` |
| List ports | - | `connect list` or `devs` |

## Features

| Feature | mpytool | mpremote |
|---------|---------|----------|
| **Connection** | | |
| Auto-detect port | Yes | Yes |
| TCP/socket connection | Yes (`-a ip:port`) | - |
| Connect by serial ID | - | Yes (`id:serial`) |
| Port shortcuts | - | Yes (`a0`, `u0`, `c0`) |
| Multiple connections | - | Yes |
| **File Transfer** | | |
| Skip unchanged files | Yes (SHA256) | Yes (SHA256) |
| Force upload | Yes (`-f`) | Yes (`-f`) |
| Recursive copy | Automatic | With `-r` flag |
| Compression | Auto (deflate) | - |
| Chunk size | Auto-detect (512B-32KB) | Fixed |
| Compile .py to .mpy | Yes (`-m` flag) | - |
| Copy contents only | Yes (trailing `/`) | - |
| **Output** | | |
| Progress bar | Yes | Yes |
| Verbose output | Yes (`-v`) | Yes (`-v`) |
| Quiet mode | Yes (`-q`) | - |
| Debug levels | Yes (`-d/-dd/-ddd`) | - |
| Color output | Yes (NO_COLOR aware) | Yes |
| **REPL** | | |
| Session capture | - | Yes (`--capture`) |
| Code injection | - | Yes (Ctrl-J) |
| File injection | - | Yes (Ctrl-K) |
| Escape non-printable | - | Yes (`-e`) |
| Exit shortcut | Ctrl+] | Ctrl+] or Ctrl+X |
| Show CWD/path on start | Yes (`-v`) | - |
| **Reset Options** | | |
| Soft reset | Yes | Yes |
| Machine reset | Yes | Yes |
| Raw REPL reset | Yes (`--raw`) | - |
| Hardware reset (RTS) | Yes (`--rts`) | - |
| DTR bootloader (ESP32) | Yes (`--dtr-boot`) | - |
| **Mount VFS** | | |
| Read-only mount | Yes | Yes |
| Read-write mount | Yes (`-w`) | Yes (default) |
| Custom mount point | Yes (any path) | - (`/remote` only) |
| Multiple mounts | Yes | - |
| Virtual submounts | Yes (`ln` command) | - |
| Transparent .mpy | Yes (`-m` flag) | - |
| Soft reset remount | Yes | Yes |
| CWD restore after reset | Yes | - |
| Unsafe symlinks | - | Yes (`-l`) |
| **Advanced** | | |
| Flash operations | Yes (RP2, ESP32) | - |
| OTA firmware update | Yes (ESP32) | - |
| Package manager | - | Yes (mip) |
| RTC control | - | Yes |
| ROMFS support | - | Yes |
| **Usability** | | |
| Shell completion | Yes (ZSH + Bash) | - |
| Remote path completion | Yes | - |
| User config file | - | Yes (`config.py`) |
| Custom aliases | - | Yes |
| Command separator | `--` | `+` |
| Default (no command) | Error message | Enter REPL |
| CWD tracking | Yes (`cd`, `pwd`) | - |
| sys.path control | Yes (`path`) | - |
| Python API | Yes (documented) | Planned |
| Raw-paste mode | Yes | Yes |
| **Platform Support** | | |
| Linux | Yes | Yes |
| macOS | Yes | Yes |
| Windows | Yes | Yes |

## Path Syntax

| Syntax | mpytool | mpremote |
|--------|---------|----------|
| Device path prefix | `:` required | `:` optional |
| Current directory | `:` | `.` or empty |
| Root directory | `:/` | `/` |
| Relative path | `:path` | `path` |
| Absolute path | `:/path` | `/path` |
| Copy contents only | `src/` (trailing slash) | Not supported |

## Mount VFS Comparison

| Feature | mpytool | mpremote |
|---------|---------|----------|
| Read-only mount | Yes | Yes |
| Read-write mount | Yes (`-w`) | Yes |
| Custom mount point | Yes (any path) | No (fixed `/remote`) |
| Multiple mounts | Yes | No (single mount) |
| Virtual submounts | Yes (`ln` cmd) | No |
| Transparent .mpy | Yes (`-m` flag) | No |
| Soft reset remount | Yes | Yes |
| Path protection | Yes (realpath) | Yes (realpath) |
| Unsafe symlinks | No | Yes (`--unsafe-links`) |
| VFS RENAME | No | Yes |
| VFS SEEK | No | Yes |
| VFS READLINE | No | Yes |
| Iterative listdir | No | Yes |
| Agent size | ~3.5KB | ~2.5KB (compressed) |

## Summary

**mpytool advantages:**
- TCP/socket connection for network devices
- Automatic compression and chunk size optimization
- `.mpy` compilation during upload and mount
- Flash operations and OTA updates (RP2, ESP32)
- Flexible mount options (custom paths, multiple mounts, submounts)
- Shell completion with remote path support
- CWD and sys.path tracking across commands

**mpremote advantages:**
- Package manager (mip) for micropython-lib
- RTC and ROMFS support
- REPL session capture and code/file injection
- User config file and custom aliases
- More VFS operations (SEEK, READLINE, RENAME, iterative listdir)
- Connect by device serial ID
- Smaller VFS agent size
