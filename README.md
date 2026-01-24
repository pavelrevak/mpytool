# mpytool

MPY tool - manage files on devices running MicroPython

It is an alternative to [ampy](https://github.com/scientifichackers/ampy)

Target of this project is to make more clean code, faster, better verbose output...

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

upload file or whole directory:
```
$ mpytool -p /dev/ttyACM0 put boot.py
$ mpytool -vp /dev/ttyACM0 put app/lib
$ mpytool -vp /dev/ttyACM0 --exclude-dir build --exclude-dir dist put app/src light_control
```

view content of file or download:
```
$ mpytool -p /dev/ttyACM0 get boot.py
$ mpytool -p /dev/ttyACM0 get net.py >> net.py
```

make directory, erase dir or files:
```
$ mpytool -p /dev/ttyACM0 mkdir a/b/c/d xyz/abc
$ mpytool -p /dev/ttyACM0 delete a xyz
```

reset only, reset and follow output, REPL mode:
```
$ mpytool -p /dev/ttyACM0 reset
$ mpytool -p /dev/ttyACM0 reset follow
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
$ mpytool -p /dev/ttyACM0 put main.py / -- reset -- follow
$ mpytool -p /dev/ttyACM0 delete old.py -- put new.py / -- reset
```

auto-detect serial port (if only one device is connected):
```
$ mpytool ls
$ mpytool -v put main.py /
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

command aliases:
- `dir` = `ls`
- `cat` = `get`
- `del`, `rm` = `delete`

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

## Verbose and debug output

use `-v` or `-vv` to show verbose output (like currently processing file, ..)
- normally print only errors (red)
- `-d` print warnings (yellow)
- `-dd` print info messages (purple)
- `-ddd` print debug messages (blue)

for reporting bugs, please provide in to issue also -ddd messages

## MPYTOOL vs other tools

for test used: ESP32S2 over USB 2MB RAM and 4MB FLASH:

recursive put of 30 files in 4 folders, 70KB total:

- mpytool: 12.3s
- mpremote: 16.5s
- ampy: 79.3s
- rshell: 81.1s

## Requirements

Working only with MicroPython boards, not with CircuitPython

- python v3.10+
- pyserial v3.0+

### Running on:

- Linux
- MacOS
- Windows (REPL mode is disabled)

## Credits

(c) 2022 by Pavel Revak

### License

MIT

### Support

- Basic support is free over GitHub issues.
- Professional support is available over email: [Pavel Revak](mailto:pavel.revak@gmail.com?subject=[GitHub]%20mpytool).
