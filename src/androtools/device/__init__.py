# 只负责 Android 设备的初始化
from androtools.device.abc import (
    Device,
    DeviceConsole,
    DeviceInfo,
)
from androtools.device.constants import Android_API_MAP, KeyEvent
from androtools.device.google import GEmu
from androtools.device.nox import NoxConsole, NoxPlayer, NoxPlayerInfo

__all__ = [
    "GEmu",
    "KeyEvent",
    "Android_API_MAP",
    # "DeviceManager",
    "Device",
    "DeviceConsole",
    # "DeviceStatus",
    # "WorkStatus",
    "DeviceInfo",
    "NoxPlayer",
    "NoxConsole",
    "NoxPlayerInfo",
]
