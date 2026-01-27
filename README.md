# mpytool

MPY tool - manage files on devices running MicroPython

It is an alternative to the official [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html).

Target of this project is cleaner code, better performance, and improved verbose output.

## Installation

```
pip3 install mpytool
```

### Installation in virtualenv

Create a dedicated virtualenv for CLI tools (keeps your system Python clean):

```bash
# Create virtualenv (once)
python3 -m venv ~/.venv/tools

# Install mpytool
~/.venv/tools/bin/pip install mpytool

# Run directly
~/.venv/tools/bin/mpytool --help
```

To use `mpytool` command without full path, add the venv bin to end of your PATH:

**ZSH** (`~/.zshrc`):
```bash
export PATH="$PATH:$HOME/.venv/tools/bin"
```

**Bash** (`~/.bashrc`):
```bash
export PATH="$PATH:$HOME/.venv/tools/bin"
```

Then restart your shell (`exec zsh` or `exec bash`) and use `mpytool` directly.

Adding venv bin at the end of PATH keeps your system `python` and `pip` as default, while making `mpytool` available when not found elsewhere.

## Examples:

help:
```
$ mpytool --help
```

list files:
```
$ mpytool -p /dev/ttyACM0 ls
$ mpytool -p /dev/ttyACM0 ls lib
```

tree files:
```
$ mpytool -p /dev/ttyACM0 tree
```

copy files (: prefix = device path):
```
$ mpytool cp main.py :/             # upload file to device root
$ mpytool cp main.py lib.py :/lib/  # upload multiple files to directory
$ mpytool cp myapp/ :/              # upload directory (creates /myapp/)
$ mpytool cp myapp/ :/lib/          # upload directory into /lib/
$ mpytool cp :/main.py ./           # download file to current directory
$ mpytool cp :/ ./backup/           # download entire device to backup/
$ mpytool cp :/old.py :/new.py      # copy file on device
$ mpytool cp -f main.py :/          # force upload even if unchanged
```

Unchanged files are automatically skipped (compares size and SHA256 hash).
Use `-f` or `--force` to upload all files regardless.

move/rename on device:
```
$ mpytool mv :/old.py :/new.py      # rename file
$ mpytool mv :/file.py :/lib/       # move file to directory
$ mpytool mv :/a.py :/b.py :/lib/   # move multiple files to directory
```

legacy upload/download (still available):
```
$ mpytool put boot.py /
$ mpytool get boot.py >> boot.py
```

make directory, delete files:
```
$ mpytool mkdir a/b/c/d xyz/abc   # create directories
$ mpytool rm mydir                # delete directory and contents
$ mpytool rm mydir/               # delete contents only, keep directory
$ mpytool rm /                    # delete everything on device
```

reset only, reset and monitor output, REPL mode:
```
$ mpytool -p /dev/ttyACM0 reset
$ mpytool -p /dev/ttyACM0 reset monitor
$ mpytool -p /dev/ttyACM0 repl
```

execute Python code on device:
```
$ mpytool exec "print('Hello!')"
$ mpytool exec "import sys; print(sys.version)"
```

show device information:
```
$ mpytool info
Platform:    rp2
Version:     3.4.0; MicroPython v1.27.0 on 2025-12-09
Impl:        micropython
Machine:     Raspberry Pi Pico with RP2040
Serial:      e660123456789abc
Memory:      36.4 KB / 240 KB (15.15%)
Flash:       120 KB / 1.38 MB (8.52%)
```

On devices with WiFi or Ethernet, MAC addresses are also shown:
```
MAC WiFi:    aa:bb:cc:dd:ee:01
MAC WiFi AP: aa:bb:cc:dd:ee:02
```

multiple commands separated by `--`:
```
$ mpytool cp main.py boot.py :/ -- reset -- monitor
$ mpytool delete old.py -- cp new.py :/ -- reset
```

auto-detect serial port (if only one device is connected):
```
$ mpytool ls lib/
Using /dev/tty.usbmodem1101
          uhttp/
  23.2 KB wlan.py
  4.95 KB wlan_http.py
```

tree view:
```
$ mpytool tree
Using /dev/tty.usbmodem1101
   142 KB ./
  41.3 KB ├─ html/
    587 B │  ├─ index.html
  40.8 KB │  └─ wlan.html
  97.7 KB ├─ lib/
  69.6 KB │  ├─ uhttp/
     93 B │  │  ├─ __init__.py
  26.3 KB │  │  ├─ client.py
  43.2 KB │  │  └─ server.py
  23.2 KB │  ├─ wlan.py
  4.95 KB │  └─ wlan_http.py
     23 B ├─ boot.py
  3.03 KB └─ main.py
```

tree view:
```
$ mpytool tree
Using /dev/tty.usbmodem11101
  150852 ./
   42342 ├─ html/
     587 │  ├─ index.html
   41755 │  └─ wlan.html
  100059 ├─ lib/
   71267 │  ├─ uhttp/
      93 │  │  ├─ __init__.py
   26963 │  │  ├─ client.py
   44211 │  │  └─ server.py
   23726 │  ├─ wlan.py
    5066 │  └─ wlan_http.py
      29 ├─ boot.py
    3102 └─ main.py
```

connect over network (TCP, default port 23):
```
$ mpytool -a 192.168.1.100 ls
$ mpytool -a 192.168.1.100:8266 tree
```

set baudrate (default 115200):
```
$ mpytool -p /dev/ttyACM0 -b 9600 ls
```

show version:
```
$ mpytool -V
```

Command aliases:
- `dir` = `ls`
- `cat` = `get`
- `del`, `rm` = `delete`
- `follow` = `monitor`

## Examples using API from Python

```
>>> import mpytool
>>> conn = mpytool.ConnSerial(port='/dev/ttyACM0', baudrate=115200)
>>> mpy = mpytool.Mpy(conn)
>>> mpy.ls()
[('ehome', None), ('boot.py', 215), ('net.py', 2938), ('project.json', 6404)]
>>> mpy.mkdir('a/b/c')
>>> mpy.ls()
[('a', None),
 ('ehome', None),
 ('boot.py', 215),
 ('net.py', 2938),
 ('project.json', 6404)]
>>> mpy.get('boot.py')
b"import time\nimport net\n\nwlan = net.Wlan()\nwlan.refresh_network()\n\nwhile wlan.ifconfig()[0] == '0.0.0.0':\n    time.sleep(.1)\n\nprint('IP: ' + wlan.ifconfig()[0])\n\nimport ehome.ehome\n\nehome.ehome.start('project.json')\n"
>>> mpy.delete('a/b')
```

## Progress and verbose output

Progress is shown by default during file transfers:
```
$ mpytool cp main.py lib.py :/lib/
[1/2] 100%   1.2KB main.py -> :/lib/main.py
[2/2] 100%   3.4KB lib.py  -> :/lib/lib.py
```

use `-v` or `--verbose` to also show commands being executed:
```
$ mpytool -v rm /old.py
delete: /old.py
```

use `-q` or `--quiet` to disable all output:
```
$ mpytool -q cp main.py :/
```

## Output Example

Complete workflow - upload changed files, reset device, and monitor output:
```
$ mpytool cp ~/Work/mpy/wlan/main.py ~/Work/mpy/wlan/html :/ -- cp ~/Work/mpy/wlan/wlan_http.py ~/Work/mpy/wlan/wlan.py ~/Work/mpy/uhttp/uhttp :/lib/ -- reset -- monitor
Using /dev/tty.usbmodem1101
COPY
  [1/8] 100% 3.03K Work/mpy/wlan/main.py            -> :/main.py
  [2/8] skip  587B Work/mpy/wlan/html/index.html       (unchanged)
  [3/8] skip 40.8K Work/mpy/wlan/html/wlan.html        (unchanged)
  [4/8] skip 4.95K Work/mpy/wlan/wlan_http.py          (unchanged)
  [5/8] 100% 23.2K Work/mpy/wlan/wlan.py            -> :/lib/wlan.py
  [6/8] skip 43.2K Work/mpy/uhttp/uhttp/server.py      (unchanged)
  [7/8] skip 26.3K Work/mpy/uhttp/uhttp/client.py      (unchanged)
  [8/8] skip   93B Work/mpy/uhttp/uhttp/__init__.py    (unchanged)
  26.2K  17.3K/s  1.5s  speedup 5.4  (2 transferred, 6 skipped)
RESET
MONITOR (Ctrl+C to stop)

starting web...
Config file not created
AP started: ESP32 (WPA2_PSK, IP: 192.168.4.1)
Scanning...
```

## Debug output

- `-d` print warnings (yellow)
- `-dd` print info messages (purple)
- `-ddd` print debug messages (blue)

For reporting bugs, please include `-ddd` output in the issue.

## Performance

Benchmark comparison with mpremote (native USB, January 2026):

### ESP32-C6 (native USB)

| Test | mpytool | mpremote | Speedup |
|------|---------|----------|---------|
| Small files upload (50 x 4KB) | 6.5s (30.8 KB/s) | 16.4s (12.2 KB/s) | **2.5x** |
| Small files download | 4.9s (41.0 KB/s) | 18.9s (10.6 KB/s) | **3.9x** |
| Large files upload (5 x 40KB) | 5.7s (35.1 KB/s) | 12.5s (16.1 KB/s) | **2.2x** |
| Large files download | 4.0s (50.4 KB/s) | 13.3s (15.0 KB/s) | **3.4x** |
| Re-upload unchanged | 0.7s | 1.8s | **2.6x** |

### RP2040 (native USB)

| Test | mpytool | mpremote | Speedup |
|------|---------|----------|---------|
| Small files upload (50 x 4KB) | 8.2s (24.6 KB/s) | 19.1s (10.5 KB/s) | **2.3x** |
| Small files download | 5.9s (33.9 KB/s) | 17.6s (11.4 KB/s) | **3.0x** |
| Large files upload (5 x 40KB) | 7.0s (28.7 KB/s) | 15.2s (13.2 KB/s) | **2.2x** |
| Large files download | 5.2s (38.8 KB/s) | 13.1s (15.3 KB/s) | **2.5x** |
| Re-upload unchanged | 0.6s | 1.6s | **2.5x** |

### ESP32 (over UART) - mpremote fails on Mac OS with devices over UART

| Test | mpytool | mpremote | Speedup |
|------|---------|----------|---------|
| Small files upload (50 x 4KB) | 8.2s (24.6 KB/s) | | |
| Small files download | 5.9s (33.9 KB/s) | | |
| Large files upload (5 x 40KB) | 7.0s (28.7 KB/s) | | |
| Large files download | 5.2s (38.8 KB/s) | | |
| Re-upload unchanged | 0.6s | | |

### mpytool advantages

- **2-4x faster** file transfers than mpremote
- **Skip unchanged files** - compares size + SHA256 hash (re-upload in <1s)
- **Robust REPL recovery** - works reliably with ESP32 via USB-UART bridges (CP2102, CH340) where mpremote often fails with "could not enter raw repl"
- Progress indicator with file counts (`[3/10] 50% file.py -> :/lib/`)
- Single tool for all operations (no need to chain commands)
- Clean verbose output (`-v`) for debugging

## Shell Completion

Tab completion for ZSH and Bash with support for commands, options, and remote file paths on the device.

### ZSH (one-liner install)

```bash
mkdir -p ~/.zsh/completions && curl -fsSL https://raw.githubusercontent.com/pavelrevak/mpytool/main/completions/_mpytool -o ~/.zsh/completions/_mpytool && echo 'fpath=(~/.zsh/completions $fpath); autoload -Uz compinit && compinit' >> ~/.zshrc && exec zsh
```

Or step by step:
```bash
# Download completion file
mkdir -p ~/.zsh/completions
curl -fsSL https://raw.githubusercontent.com/pavelrevak/mpytool/main/completions/_mpytool -o ~/.zsh/completions/_mpytool

# Add to ~/.zshrc (if not already there)
echo 'fpath=(~/.zsh/completions $fpath); autoload -Uz compinit && compinit' >> ~/.zshrc

# Restart shell
exec zsh
```

### Bash (one-liner install)

Linux:
```bash
sudo curl -fsSL https://raw.githubusercontent.com/pavelrevak/mpytool/main/completions/mpytool.bash -o /etc/bash_completion.d/mpytool && exec bash
```

macOS (Homebrew):
```bash
curl -fsSL https://raw.githubusercontent.com/pavelrevak/mpytool/main/completions/mpytool.bash -o /usr/local/etc/bash_completion.d/mpytool && exec bash
```

Or manually to home directory:
```bash
curl -fsSL https://raw.githubusercontent.com/pavelrevak/mpytool/main/completions/mpytool.bash -o ~/.mpytool-completion.bash
echo 'source ~/.mpytool-completion.bash' >> ~/.bashrc
exec bash
```

### Features

- Tab completion for all commands and aliases
- Remote file/directory completion (cached for 60 seconds)
- Support for `--` command separator
- Works with both relative and absolute paths

Clear cache: `_mpytool_clear_cache`

## Requirements

Working only with MicroPython boards, not with CircuitPython

- python v3.10+
- pyserial v3.0+

### Running on:

- Linux
- MacOS
- Windows (limited support - REPL mode is disabled)

## Credits

(c) 2022-2026 by Pavel Revak

### License

MIT

### Support

- Basic support is free over GitHub issues.
- Professional support is available over email: [Pavel Revak](mailto:pavel.revak@gmail.com?subject=[GitHub]%20mpytool).
