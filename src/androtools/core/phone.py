from enum import Enum

from func_timeout import FunctionTimedOut, func_timeout

from androtools import logger
from androtools.android_sdk.emulator import Emulator
from androtools.android_sdk.platform_tools import ADB
from androtools.core.constants import Android_API_MAP
from androtools.core.device import Device, DeviceInfo


class STATE(Enum):
    DEVICE = "device"
    RECOVERY = "recovery"
    RESCUE = "rescue"
    SIDELOADING = "sideload"
    BOOTLOADER = "bootloader"
    DISCONNECT = "disconnect"


class TRANSPORT(Enum):
    USB = "usb"
    LOCAL = "local"
    ANY = "any"


class G_STATE(Enum):
    """使用 get_state 方法获取的状态。"""

    DEVICE = "device"
    OFFLINE = "offline"
    BOOTLOADER = "bootloader"
    NOFOUND = "nofound"
    UNKNOWN = "unknown"


class Phone(Device):
    def __init__(self, info: DeviceInfo):
        super().__init__(info)
        self.pid = 1000

    def get_pid(self) -> int:
        return self.pid

    def launch(self):
        pass

    def close(self):
        pass

    def reboot(self):
        pass
