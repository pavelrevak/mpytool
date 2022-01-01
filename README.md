# mpytool

MicroPython tool

Control device running MicroPython over serial line. Allow list files, upload, download, delete, ...

It is an alternative to [ampy](https://github.com/scientifichackers/ampy)

Target of this project is to make it more clean code, faster, better verbose output...

## Installation

```
pip3 install https://github.com/pavelrevak/mpytool.git
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
$ mpytool -vp /dev/ttyACM0 put app/libs lib
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

## Examples using API from Python

```
>>> import mpytool
>>> conn = mpytool.conn_serial.ConnSerial(port='/dev/ttyACM0', baudrate=115200)
>>> mpy = mpytool.mpy.Mpy(conn)
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
```

## Requirements

- Working only with MicroPython boards, not with CircuitPython

- python v3.6+
- pyserial v3.0+

### Running on:

- Linux
- MacOS
- Windows

## Credits

(c) 2022 by Pavel Revak

### Support

- Basic support is free over GitHub issues.
- Professional support is available over email: [Pavel Revak](mailto:pavel.revak@gmail.com?subject=[GitHub]%20mpytool).
