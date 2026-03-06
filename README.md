# androtools

[![PyPI](https://img.shields.io/pypi/v/androtools?style=flat-square)](https://pypi.org/project/androtools/) ![PyPI - Status](https://img.shields.io/pypi/status/androtools?style=flat-square) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/androtools?style=flat-square) ![PyPI - Downloads](https://img.shields.io/pypi/dw/androtools?style=flat-square) ![PyPI - License](https://img.shields.io/pypi/l/androtools?style=flat-square)

一个对Android SDK相关的命令封装的库。

## V2 说明

- ADB
- Device
  - 启动，一个关闭的手机，无法通过命令行启动的。
  - 关闭
  - 重启
  - 连接，对于远程设备
    - connect
    - adb devices，查找目标设备。
  - 断开，针对远程设备
    - disconnect
    - usb，则什么都不执行，没用。
- DeviceControllor
  - 设备控制器
  - 具体的操作
