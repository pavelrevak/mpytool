# mpytool vs mpremote Comparison

Detailed comparison between [mpytool](https://github.com/pavelrevak/mpytool) and [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html).

## Commands

| Command | mpytool | mpremote |
|---------|---------|----------|
| List files | ✓ `ls :/path` | ✓ `ls path` |
| Tree view | ✓ `tree :/path` | ✓ `tree path` |
| Print file | ✓ `cat :file` | ✓ `cat file` |
| Copy files | ✓ `cp src :dst` | ✓ `cp src :dst` |
| Delete file | ✓ `rm :file` | ✓ `rm file` |
| Delete dir | ✓ `rm :dir` | ✓ `rmdir dir` |
| Create dir | ✓ `mkdir :dir` | ✓ `mkdir dir` |
| Move/rename | ✓ `mv :src :dst` | ✗ |
| Touch file | ✗ | ✓ `touch file` |
| SHA256 hash | ✗ | ✓ `sha256sum file` |
| Execute code | ✓ `exec "code"` | ✓ `exec "code"` |
| Evaluate expr | ✗ | ✓ `eval "expr"` |
| Run script | ✓ `run script.py` | ✓ `run script.py` |
| Enter REPL | ✓ `repl` | ✓ `repl` |
| Monitor output | ✓ `monitor` | ✗ use `repl` |
| Soft reset | ✓ `reset` | ✓ `soft-reset` |
| Machine reset | ✓ `reset --machine` | ✓ `reset` |
| Hardware reset | ✓ `reset --rts` | ✗ |
| Bootloader | ✓ `reset --boot` | ✓ `bootloader` |
| Device info | ✓ `info` | ✗ use `exec` |
| Disk usage | ✓ `info` | ✓ `df` |
| Speed test | ✓ `speedtest` | ✗ |
| Mount VFS | ✓ `mount ./dir :/mp` | ✓ `mount ./dir` |
| Unmount VFS | ✗ exit session | ✓ `umount` |
| Virtual submount | ✓ `ln ./src :/dst` | ✗ |
| Package install | ✗ | ✓ `mip install pkg` |
| RTC control | ✗ | ✓ `rtc`, `rtc --set` |
| ROMFS manage | ✗ | ✓ `romfs` |
| Edit remote file | ✗ | ✓ `edit :file` |
| Flash read/write | ✓ `flash r/w/erase` | ✗ |
| OTA update | ✓ `ota firmware.app-bin` | ✗ |
| Print CWD | ✓ `pwd` | ✗ use `exec` |
| Change CWD | ✓ `cd :path` | ✗ use `exec` |
| Manage sys.path | ✓ `path` | ✗ use `exec` |
| Sleep | ✓ `sleep 2` (sec) | ✓ `sleep 2000` (ms) |
| Connect device | ✓ auto / `-p` | ✓ `connect dev` |
| Disconnect | ✗ | ✓ `disconnect` |
| Resume session | ✗ not needed | ✓ `resume` |
| List ports | ✓ auto | ✓ `devs` |

## Features

| Feature | mpytool | mpremote |
|---------|---------|----------|
| **Connection** | | |
| Auto-detect port | ✓ | ✓ |
| TCP/socket connection | ✓ `-a ip:port` | ✗ |
| Connect by serial ID | ✗ | ✓ `id:serial` |
| Port shortcuts | ✗ | ✓ `a0`, `u0`, `c0` |
| Multiple connections | ✗ | ✓ switching |
| **File Transfer** | | |
| Skip unchanged files | ✓ SHA256 | ✓ SHA256 |
| Force upload | ✓ `-f` | ✓ `-f` |
| Recursive copy | ✓ auto | ✓ `-r` flag |
| Compression | ✓ deflate | ✗ |
| Chunk size | ✓ auto 512B-32KB | ✗ 256B fixed |
| Compile .py to .mpy | ✓ `-m` | ✗ |
| **Output** | | |
| Progress bar | ✓ | ✓ |
| Verbose output | ✓ `-v` | ✓ `-v` |
| Quiet mode | ✓ `-q` | ✗ |
| Debug levels | ✓ `-d/-dd/-ddd` | ✗ |
| Color output | ✓ NO_COLOR aware | ✓ |
| **REPL** | | |
| Session capture | ✗ | ✓ `--capture` |
| Code injection | ✗ | ✓ Ctrl-J |
| File injection | ✗ | ✓ Ctrl-K |
| Escape non-printable | ✗ | ✓ `-e` |
| Exit shortcut | ✓ Ctrl+] | ✓ Ctrl+] / Ctrl+X |
| Show CWD/path on start | ✗ | ✗ |
| **Reset Options** | | |
| Soft reset | ✓ | ✓ |
| Machine reset | ✓ | ✓ |
| Raw REPL reset | ✓ `--raw` | ✗ |
| Hardware reset (RTS) | ✓ `--rts` | ✗ |
| DTR bootloader (ESP32) | ✓ `--dtr-boot` | ✗ |
| **Advanced** | | |
| Flash operations | ✓ RP2, ESP32 | ✗ |
| OTA firmware update | ✓ ESP32 | ✗ |
| Package manager | ✗ | ✓ mip |
| RTC control | ✗ | ✓ |
| ROMFS support | ✗ | ✓ |
| **Usability** | | |
| Shell completion | ✓ ZSH + Bash | ✗ |
| Remote path completion | ✓ | ✗ |
| User config file | ✗ | ✓ `config.py` |
| Custom aliases | ✗ | ✓ |
| Command separator | ✓ `--` | ✓ `+` |
| Default (no command) | ✓ info | ✓ REPL |
| CWD tracking | ✓ `cd`, `pwd` | ✗ |
| sys.path control | ✓ `path` | ✗ |
| Python API | ✓ documented | ✗ planned |
| Raw-paste mode | ✓ | ✓ |
| **Platform Support** | | |
| Linux | ✓ | ✓ |
| macOS | ✓ | ✓ |
| Windows | ✓ | ✓ |

## Path Syntax

| Syntax | mpytool | mpremote |
|--------|---------|----------|
| Device path prefix | ✓ `:` required | ✓ `:` optional |
| Current directory | ✓ `:` | ✓ `.` or empty |
| Root directory | ✓ `:/` | ✓ `/` |
| Relative path | ✓ `:path` | ✓ `path` |
| Absolute path | ✓ `:/path` | ✓ `/path` |
| Copy contents only | ✓ `src/` trailing slash | ✗ |

## Mount VFS Comparison

| Feature | mpytool | mpremote |
|---------|---------|----------|
| Read-only mount | ✓ default | ✗ |
| Read-write mount | ✓ `-w` | ✓ always |
| Custom mount point | ✓ any path | ✗ `/remote` only |
| Multiple mounts | ✓ | ✗ single mount |
| Virtual submounts | ✓ `ln` cmd | ✗ |
| Transparent .mpy | ✓ `-m` | ✗ |
| Soft reset remount | ✓ | ✓ |
| CWD restore after remount | ✓ | ✓ |
| Path protection | ✓ realpath | ✓ realpath |
| Unsafe symlinks | ✗ | ✓ `--unsafe-links` |
| VFS RENAME | ✓ | ✓ |
| VFS SEEK | ✓ | ✓ |
| VFS READLINE | ✓ | ✓ |
| File iteration | ✓ | ✓ |
| Iterative listdir | ✗ | ✓ |
| Agent size | ✓ 4.2KB raw | ✗ 5.5KB compressed |

## Summary

**mpytool advantages:**
- Faster file transfers (2-5x) — deflate compression
- TCP/socket connection for network devices
- `.mpy` compilation during upload and mount
- Flash operations and OTA updates (RP2, ESP32)
- Flexible mount options (custom paths, multiple mounts, submounts, read-only)
- Shell completion with remote path support
- CWD and sys.path tracking across commands
- No auto soft-reset (preserves device state between commands)
- Smaller VFS agent (4.2KB vs 5.5KB) — 24% less RAM, faster mount
- Minimalist design (blocking I/O, simpler code)

**mpremote advantages:**
- Package manager (mip) for micropython-lib
- RTC and ROMFS support
- REPL session capture and code/file injection
- User config file and custom aliases
- More VFS operations (iterative listdir for large directories)
- VFS timeout protection (polling)
- Connect by device serial ID
