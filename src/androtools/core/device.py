# Android模拟器、雷电模拟器的基类
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import psutil

from androtools.android_sdk.platform_tools import ADB
from androtools.cmd import CMD
from androtools.cmd.result import CmdResult
from androtools.core.constants import Android_API_MAP


class DeviceType(Enum):
    """模拟器类型"""

    PHONE = "phone"
    """手机"""
    LD = "ld"
    """雷电模拟器"""
    NOX = "nox"
    UNKNOWN = "unknown"

    @staticmethod
    def get(value: str):
        value = value.lower()
        for item in DeviceType:
            if item.value == value:
                return item
        return DeviceType.UNKNOWN


@dataclass
class DeviceInfo:
    """模拟器信息"""

    device_type: DeviceType = DeviceType.PHONE
    index: str = "0"  # 模拟器序号，雷电模拟器、夜神模拟器的序号
    serial: Optional[str] = None  # 模拟器序列号，adb -s 的操作对象
    name: str = ""  # 模拟器名称，它可以修改。
    version: int = 9  # 模拟器版本, 如 9 表示 Android 9
    sdk: int = 28
    adb_path: str = "adb"  # adb 路径
    console_path: str = ""  # 模拟器控制器；雷电模拟器则是 ldconsole
    gateway: str = ""  # 网关IP
    proxy_port: int = 8080  # mitmproxy 代理端口

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, DeviceInfo):
            return False

        return (
            self.index == __value.index
            and self.adb_path == __value.adb_path
            and self.console_path == __value.console_path
        )

    def __repr__(self) -> str:
        return f"{self.index} {self.name}"


class DeviceConsole(CMD):
    """模拟器控制台，用于控制模拟器的启动和关闭。"""

    @abstractmethod
    def launch_device(self, idx: int | str) -> CmdResult:
        """启动模拟器"""
        pass

    @abstractmethod
    def reboot_device(self, idx: int | str) -> CmdResult:
        """重启模拟器"""
        pass

    @abstractmethod
    def quit_device(self, idx: int | str) -> CmdResult:
        """关闭模拟器"""
        pass

    @abstractmethod
    def quit_all_devices(self) -> CmdResult:
        """关闭所有的模拟器"""
        pass

    @abstractmethod
    def list_devices(self) -> str:
        """列出所有模拟器信息"""
        pass

    @abstractmethod
    def install_app(self, idx: int | str, apk_path) -> CmdResult:
        pass

    @abstractmethod
    def uninstall_app(self, idx: int | str, package_name) -> CmdResult:
        pass


class Device(ABC):
    def __init__(self, info: DeviceInfo) -> None:
        self.info = info
        self.name = info.name
        self.android_version = info.version
        self.adb: ADB = ADB(info.adb_path)
        self._is_busy = False
        self.console = DeviceConsole(self.info.console_path)

        self.pid = -1
        self.init_pid()
        self.sdk = self.get_sdk()

    @abstractmethod
    def get_pid(self) -> int:
        pass

    def init_pid(self):
        self.pid = self.get_pid()

    def get_memory_rss(self):
        """获取常驻内存大小字节"""
        if self.pid == -1:
            return 0
        proc = psutil.Process(self.pid)
        mem_info = proc.memory_info()
        return mem_info.rss

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @is_busy.setter
    def is_busy(self, value: bool) -> None:
        self._is_busy = value

    def get_android_os_name(self):
        if self.sdk is None:
            self.sdk = self.get_sdk()

        self.android_version = "Unknown"
        if result := Android_API_MAP.get(self.sdk):
            self.android_version = result[0]

    def __str__(self) -> str:
        return f"{self.info.name}-{self.android_version}"

    def is_boot(self) -> bool:
        """判断设备是否已经启动"""
        # 如果已经有进程ID，则表示已经启动
        return self.pid != -1

    def getprop(self, prop: str | None = None) -> str:
        """获取模拟器属性"""
        if prop:
            result = self.adb.run_shell_cmd(["getprop", prop])
        else:
            result = self.adb.run_shell_cmd(["getprop"])

        return result.output

    def get_sdk(self):
        sdk = -1
        output = self.getprop("ro.build.version.sdk")
        if output == "":
            return sdk

        if isinstance(output, str):
            sdk = int(output)
        elif isinstance(output, list):
            sdk = int(output[0])

        return sdk

    def is_boot_completed(self) -> bool:
        r = self.getprop("sys.boot_completed")
        return r == "1"

    @abstractmethod
    def launch(self):
        """启动模拟器"""
        # NOTE: 必须设置 pid
        pass

    @abstractmethod
    def close(self):
        """关闭模拟器"""
        pass

    @abstractmethod
    def reboot(self):
        """重启模拟器"""
        pass

    def reconnect(self):
        self.adb.run_cmd(["reconnect"])


class DeviceADB:
    """ADB绑定设备，在多设备的情况下，直接使用，无需使用设备号。"""

    def __init__(self, device: Device) -> None:
        self.device = device

    def adb(self, cmd: list, timeout: int = 30) -> CmdResult:
        """执行 adb 命令"""
        return self.device.adb.run_cmd(cmd, self.device.info.serial, timeout)

    def adb_shell(self, cmd: list[str], timeout: int = 30) -> CmdResult:
        """执行 adb shell 命令"""
        assert cmd is not None
        assert isinstance(cmd, list)
        return self.device.adb.run_shell_cmd(cmd, self.device.info.serial, timeout)

    def adb_shell_daemon(self, cmd: list[str]):
        assert cmd is not None
        assert isinstance(cmd, list)
        if isinstance(cmd, str):
            raise TypeError(f"命令必须是列表：{cmd}")
        self.device.adb.run_shell_cmd_daemon(cmd, self.device.info.serial)

    def pull(self, remote: str, local: str):
        """将文件从模拟器下载到本地"""
        cmd = ["pull", remote, local]
        result = self.device.adb.run_cmd(cmd)
        return result.contain("pulled")

    def push(self, local: str, remote: str):
        """将文件从本地上传到模拟器"""
        self.device.adb.run_cmd(["push", local, remote])

    def reconnect(self):
        self.device.adb.run_cmd(["reconnect"])
