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


# adb -s emulator-5554 emu avd id
# Pixel_4_XL_API_22
# OK
# ❯ adb -s emulator-5554 emu avd name
# Pixel_4_XL_API_22
# NOTE 设备重新启动之后，端口有可能会发生。
# 启动模拟器，然后，通过 adb devices -l 获取所有的模拟器信息。
# 在通过 adb -s emulator-5554 emu avd id，来重新映射模拟器序列号。
# TODO 内置模拟器需要验证：模拟器的名字是固定的；序列号和传输ID是可变的。
class GEmuInfo(DeviceInfo):
    """设备信息，通过 adb devices -l 获取以下信息"""

    name: str  # 设备名，用于启动模拟器
    serial: str | None  # 设备序列号，用于 adb
    path: str  # adb 路径
    emu_path: str | None  # emulator 路径

    def __init__(
        self,
        name: str,
        serial: str | None,
        path: str,
        emu_path: str | None = None,
    ):
        self.name = name
        self.serial = serial
        self.path = path
        self.emu_path = emu_path  # 启动 avd

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, GEmuInfo):
            return False
        return self.name == __value.name


class GEmu(Device):
    def __init__(self, info: GEmuInfo):
        self.info = info
        self.name = info.name
        self._adb = ADB(info.path)
        self._emulator = Emulator(info.emu_path)

        # 设备初始化，则表示设备一定存在
        state = self.get_status()
        logger.debug(f"设备 {self.name} 状态: {state.value}")
        print("->>>>>>>>>>>>>>>", state)
        if state != G_STATE.DEVICE:
            raise RuntimeError(f"Device is {state.value}")

        self.sdk = 0
        self._init_sdk()
        self.android_version = "Unknown"
        if result := Android_API_MAP.get(self.sdk):
            self.android_version = result[0]

    def __str__(self) -> str:
        return f"{self.name}-{self.android_version}({self.sdk})"

    def launch(self):
        self._emulator.start_avd(self.info.name)

    def quit(self):
        # TODO 杀死模拟器的方式
        # @Pixel_XL_API_30，获取进程pid，杀死pid。
        # 再通过emulator来启动。
        # 雷电模拟器的启动方式不一样。
        pass

    # def get_status(self) -> DeviceStatus:
    # pass

    def check_device_status(self, num=5):
        """执行命令之前，先确认一下设备的状态。

        Args:
            num (int, optional): _description_. Defaults to 5.

        Returns:
            _type_: _description_
        """
        state = self._get_state()
        logger.debug(f"check_device_status - {self.name} 状态 : {state.value}")
        if state == G_STATE.DEVICE:
            return True

        counter = 0
        is_ok = False
        while counter < num:
            counter += 1
            devices = self._adb.get_devices()
            for item in devices:
                if item[0] == self.name and item[1] == "device":
                    is_ok = True
                    break

            if is_ok:
                break

        return is_ok

    def _get_state(self):
        result = self._adb.run_cmd(["get-state"])

        # 设备丢失，需要等待，或者重启 adb
        # if "not found" in error:
        if result.contain("not found"):
            return G_STATE.NOFOUND

        if result.contain("device"):
            return G_STATE.DEVICE

        # 设备无法控制
        if result.contain("offline"):
            return G_STATE.OFFLINE

        # 设备可以重启
        if result.contain("bootloader"):
            return G_STATE.BOOTLOADER

        return G_STATE.UNKNOWN

    def is_boot(self) -> bool:
        status = self._get_state()
        return status == G_STATE.DEVICE

    # def adb_shell(self, cmd: str | list, encoding: str | None = None):
    def adb_shell(self, cmd: str | list):
        """执行 adb shell 命令"""
        logger.debug(f"run shell cmd : {cmd}")
        if not self.check_device_status():
            raise RuntimeError(f"{self.name} 设备丢失。")
        if isinstance(cmd, str):
            cmd = cmd.split()
        return self._adb.run_shell_cmd(cmd, self.info.serial)

    def adb(self, cmd: str | list):
        """执行 adb 命令"""
        logger.debug(f"run cmd : {str(cmd)}")
        if isinstance(cmd, str):
            cmd = cmd.split()
        if not self.check_device_status():
            raise RuntimeError(f"{self.name} 设备丢失。")
        return self._adb.run_cmd(cmd)

    def _init_sdk(self):
        output = self.adb_shell(["getprop", "ro.build.version.sdk"]).output
        if isinstance(output, str):
            self.sdk = int(output)
        elif isinstance(output, list):
            self.sdk = int(output[0])
        return self.sdk

    def is_ok(self):
        try:
            # 点击HOME键，超过5秒没反应
            func_timeout(5, self.home)
        except FunctionTimedOut:
            return False
        return True

    def wait_for(self, state: STATE, transport: TRANSPORT = TRANSPORT.ANY):
        cmd = "wait-for"
        if transport != TRANSPORT.ANY:
            cmd += f"-{transport.value}"
        cmd += f"-{state}"
        return self.adb([cmd])

    def is_boot_completed(self) -> bool:
        """判断设备是否处于开机状态"""
        output = self.adb_shell(["getprop", "sys.boot_completed"]).output
        return "1" in output
