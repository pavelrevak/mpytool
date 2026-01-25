# mpytool

MPY tool - manage files on devices running MicroPython

It is an alternative to the official [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html) and [ampy](https://github.com/scientifichackers/ampy).

Target of this project is cleaner code, better performance, and improved verbose output.

## Installation

```
pip3 install mpytool
```

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
Memory:      36.4 KB / 240 KB (15.15%)
Flash:       120 KB / 1.38 MB (8.52%)
```

multiple commands separated by `--`:
```
$ mpytool -p /dev/ttyACM0 put main.py / -- reset -- monitor
$ mpytool -p /dev/ttyACM0 delete old.py -- put new.py / -- reset
```

auto-detect serial port (if only one device is connected):
```
$ mpytool ls
Using /dev/ttyACM0
       215 boot.py
      2938 net.py
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

## Debug output

- `-d` print warnings (yellow)
- `-dd` print info messages (purple)
- `-ddd` print debug messages (blue)

For reporting bugs, please include `-ddd` output in the issue.

## MPYTOOL vs other tools

Benchmark on RP2040 (Raspberry Pi Pico) over native USB, January 2026:

| Test | mpytool | mpremote |
|------|---------|----------|
| 50 small files, 5 dirs (200KB) | **4.2s** | 9.4s |
| 5 large files (260KB) | **8.0s** | 17.3s |
| re-upload unchanged files | **1.3s** | 5.0s |

mpytool is **2x faster** for uploads and **4x faster** for re-uploads (skips unchanged files).

mpytool advantages:
- Fastest file transfers (2x faster than mpremote)
- Skip unchanged files (compares size + SHA256 hash)
- Progress indicator with file counts (`[3/10] 50% file.py -> :/lib/`)
- Single tool for all operations (no need to chain commands)
- Clean verbose output (`-v`) for debugging

## Shell Completion

mpytool includes tab completion for ZSH and Bash with support for commands, options, and remote file paths on the device.

### ZSH

Testing (current session):
```bash
source /path/to/mpytool/completions/_mpytool
compdef _mpytool mpytool
```

Permanent installation:
```bash
# Create completions directory
mkdir -p ~/.zsh/completions

# Symlink completion file
ln -s /path/to/mpytool/completions/_mpytool ~/.zsh/completions/_mpytool

# Add to ~/.zshrc (BEFORE any compinit line):
fpath=(~/.zsh/completions $fpath)
autoload -Uz compinit && compinit

# Restart shell
exec zsh
```

### Bash

Testing (current session):
```bash
source /path/to/mpytool/completions/mpytool.bash
```

Permanent installation:
```bash
# Option 1: Add to ~/.bashrc
echo 'source /path/to/mpytool/completions/mpytool.bash' >> ~/.bashrc

# Option 2: Copy to system completions directory
# Linux:
sudo cp /path/to/mpytool/completions/mpytool.bash /etc/bash_completion.d/mpytool
# macOS with Homebrew:
cp /path/to/mpytool/completions/mpytool.bash /usr/local/etc/bash_completion.d/mpytool
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
