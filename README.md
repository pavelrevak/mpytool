# mpytool

MPY tool - manage files on devices running MicroPython

It is an alternative to the official [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html).

Target of this project is cleaner code, better performance, and improved verbose output.

## Installation

```
pip3 install mpytool
```

### Installation from git (latest development version)

```bash
pip3 install git+https://github.com/pavelrevak/mpytool.git
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

transfer options:
```
$ mpytool cp -z main.py :/           # force compression (auto-detected by default)
$ mpytool cp --no-compress data.bin :/  # disable compression
$ mpytool -c 8K cp main.py :/        # set chunk size (512, 1K, 2K, 4K, 8K, 16K, 32K)
```

Compression is auto-detected based on device RAM and deflate module availability.
Chunk size is auto-detected based on free RAM (larger chunks = faster transfer).

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

reset and REPL:
```
$ mpytool reset              # soft reset (Ctrl-D, runs boot.py/main.py)
$ mpytool sreset             # soft reset in raw REPL (clears RAM only)
$ mpytool mreset             # MCU reset (machine.reset, auto-reconnect)
$ mpytool rtsreset           # hardware reset via RTS signal (serial only)
$ mpytool bootloader         # enter bootloader (machine.bootloader)
$ mpytool dtrboot            # enter bootloader via DTR/RTS (ESP32 only)
$ mpytool reset monitor      # reset and monitor output
$ mpytool repl               # enter REPL mode
```

serial terminal and monitor (general purpose):
```
$ mpytool repl                       # auto-detect port, 115200 baud
$ mpytool -p /dev/ttyUSB0 repl       # specify port
$ mpytool -b 9600 repl               # specify baudrate
$ mpytool -p /dev/ttyUSB0 -b 9600 monitor   # monitor at 9600 baud
```

Both `repl` and `monitor` can be used as general-purpose serial tools - not just for MicroPython devices. Use them to interact with any serial device (Arduino, ESP with custom firmware, GPS modules, etc.). When only one serial port is detected, it is used automatically. Default baudrate is 115200.

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
$ mpytool cp ~/Work/mpy/wlan/main.py ~/Work/mpy/wlan/html :/ -- cp ~/Work/mpy/wlan/wlan_http.py ~/Work/mpy/wlan/wlan.py ~/Work/mpy/uhttp/uhttp :/lib/ -- cp ~/Tmp/test0.bin :/lib/ -- reset -- monitor
Using /dev/tty.usbmodem1101
COPY (chunk: 16K, compress: on)
  [1/9] 100% 3.03K ../mpy/wlan/main.py            -> :/main.py                (compressed)
  [2/9] skip  587B ../mpy/wlan/html/index.html    -> :/html/index.html        (unchanged)
  [3/9] 100% 40.8K ../mpy/wlan/html/wlan.html     -> :/html/wlan.html         (compressed)
  [4/9] skip 4.95K ../mpy/wlan/wlan_http.py       -> :/lib/wlan_http.py       (unchanged)
  [5/9] 100% 23.1K ../mpy/wlan/wlan.py            -> :/lib/wlan.py            (compressed)
  [6/9] skip 43.2K ../mpy/uhttp/uhttp/server.py   -> :/lib/uhttp/server.py    (unchanged)
  [7/9] skip 26.3K ../mpy/uhttp/uhttp/client.py   -> :/lib/uhttp/client.py    (unchanged)
  [8/9] skip   93B ../mpy/uhttp/uhttp/__init__.py -> :/lib/uhttp/__init__.py  (unchanged)
  [9/9] skip 10.0K ../../Tmp/test0.bin            -> :/lib/test0.bin          (unchanged)
  66.9K  29.7K/s  2.3s  speedup 6.5x  (3 transferred, 6 skipped)
RESET
MONITOR (Ctrl+C to stop)

starting web server...
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

Benchmark comparison with mpremote v1.27.0 (January 2026).

Test files: 50 small (4 KB) + 4 large (50 KB) Python source files (total 400 KB).
Higher speed achieved by automatic compression of text-based files during transfer.

### RP2040 - USB-CDC - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 9.6s (20.8 KB/s) | 23.3s (8.6 KB/s) | **2.4x** |
| small 50 x 4K - skip | 2.0s | 5.4s | **2.7x** |
| large 4 x 50K | 3.4s (58.8 KB/s) | 15s (13.3 KB/s) | **4.4x** |
| large 4 x 50K - skip | 0.6s | 0.9s | **1.5x** |

### ESP32-C6 - USB-CDC - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 10.4s (19.2 KB/s) | 30.7s (6.5 KB/s) | **3.0x** |
| small 50 x 4K - skip | 5.5s | 12.7s | **2.3x** |
| large 4 x 50K | 4.0s (50.0 KB/s) | 13.2s (15.2 KB/s) | **3.3x** |
| large 4 x 50K - skip | 0.7s | 1.4s | **2.0x** |

### ESP32-WROOM - USB-UART - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 32.4s (6.2 KB/s) | crash | - |
| small 50 x 4K - skip | 11.8s | crash | - |
| large 4 x 50K | 12.1s (16.5 KB/s) | crash | - |
| large 4 x 50K - skip | 3.5s | crash | - |

### ESP32-WROVER - USB-UART - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 24.5s (8.2 KB/s) | crash | - |
| small 50 x 4K - skip | 7.7s | crash | - |
| large 4 x 50K | 12.0s (16.7 KB/s) | crash | - |
| large 4 x 50K - skip | 2.2s | crash | - |

### ESP32-WROOM - USB-UART - Linux

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 25.6s (7.8 KB/s) | 66.9s (3.0 KB/s) | **2.6x** |
| small 50 x 4K - skip | 5.9s | 17.3s | **2.9x** |
| large 4 x 50K | 7.9s (25.3 KB/s) | 44.4s (4.5 KB/s) | **5.6x** |
| large 4 x 50K - skip | 1.2s | 2.0s | **1.7x** |

### ESP32-WROVER - USB-UART - Linux

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 33.9s (5.9 KB/s) | 75.0s (2.7 KB/s) | **2.2x** |
| small 50 x 4K - skip | 9.8s | 19.4s | **2.0x** |
| large 4 x 50K | 10.0s (20.0 KB/s) | 46.4s (4.3 KB/s) | **4.6x** |
| large 4 x 50K - skip | 1.6s | 2.5s | **1.6x** |

### Summary

- **Large files upload: 3.3x - 5.6x faster** than mpremote
- **Small files upload: 2.2x - 3.0x faster** than mpremote
- **Skip unchanged: 1.5x - 2.9x faster** than mpremote
- **Robust REPL handling** - works reliably with ESP32 via USB-UART bridges (CP2102, CH340) where mpremote crashes on MacOS

Many more test scenarios could be designed (different file types, sizes, mixed workloads), but in most cases mpytool would be **at least 2x faster** than mpremote.

mpytool also provides a **Python API** suitable for integration into IDEs and custom automation tools.

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
