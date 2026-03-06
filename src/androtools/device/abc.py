# Android模拟器、雷电模拟器的基类
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from os import stat


from androtools.android_sdk.adb import ADB
from androtools.cmd import CMD
from androtools.cmd.result import CmdResult


class DeviceType(Enum):
    """Android设备类型"""

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
    serial: str = ""  # 模拟器序列号，adb -s 的操作对象
    # 模拟器，只要连上了，获取这些信息是可以获取的
    name: str = ""  # 模拟器名称，它可以修改。
    version: int = 9  # 模拟器版本, 如 9 表示 Android 9
    sdk: int = 28  # 这个有必要吗？
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


class ConnectionStatus(Enum):
    """设备连接状态"""

    NOT_FOUND = ""
    """未发现；模拟器没有启动，手机没有连接电脑，远程设备没有连接"""
    DEVICE = "device"
    """设备已连接"""
    OFFLINE = "offline"
    """设备离线；adb 服务异常，设备异常，需要重连。"""
    UNAUTHORIZED = "unauthorized"
    """未授权"""
    SIDELOAD = "sideload"
    """侧载模式"""
    RECOVERY = "recovery"
    """恢复模式"""
    HOST = "host"
    """设备被配置为USB主机"""

    @staticmethod
    def get(value: int):
        for item in ConnectionStatus:
            if item.value == value:
                return item
        raise Exception("未知状态")

    def __str__(self) -> str:
        return self.value


class Device(ABC):
    """
    Android设备

    设备的启动、重启、关闭。
    不需要其他任何操作！

    设备3要素：
    1. 连接方式，usb[emulator, phone]，wifi，remote
        wifi和remote需要connect去连接。
    2. device-id, serial, adb devices[0]
    3. adb - 模拟器连接
    4. console - 模拟器控制
    """

    def __init__(self, adb_path, device_serial, console_path) -> None:
        self.serial = device_serial
        self.adb: ADB = ADB(adb_path)
        self.console = DeviceConsole(console_path)

    def get_status(self):
        """获取当前设备的连接状态"""
        devices = self.adb.get_devices()

        for name, status, _ in devices:
            if self.serial == name:
                if "device" == status:
                    return ConnectionStatus.DEVICE
                elif "offline" == status:
                    return ConnectionStatus.OFFLINE
                elif "unauthorized" == status:
                    return ConnectionStatus.UNAUTHORIZED
                else:
                    print(devices)
                    raise Exception(f"未知状态: {name} - {status}")
        return ConnectionStatus.NOT_FOUND

    def connect(self):
        """
        只有IP网络设备才需要连接。
        """
        if ":" in self.serial:
            self.adb.connect(self.serial)

    def disconnect(self):
        if ":" in self.serial:
            self.adb.disconnect(self.serial)

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

    def __str__(self) -> str:
        return f"{self.serial}"


class DeviceADB:
    """ADB绑定设备，在多设备的情况下，直接使用，无需使用设备号。"""

    def __init__(self, device: Device) -> None:
        self.device = device

    def adb(self, cmd: list, timeout: int = 30) -> CmdResult:
        """执行 adb 命令"""
        return self.device.adb.run_cmd(cmd, self.device.serial, timeout)

    def adb_shell(self, cmd: list[str], timeout: int = 30) -> CmdResult:
        """执行 adb shell 命令"""
        assert cmd is not None
        assert isinstance(cmd, list)
        return self.device.adb.run_shell_cmd(cmd, self.device.serial, timeout)

    def adb_shell_daemon(self, cmd: list[str]):
        assert cmd is not None
        assert isinstance(cmd, list)
        if isinstance(cmd, str):
            raise TypeError(f"命令必须是列表：{cmd}")
        self.device.adb.run_shell_cmd_daemon(cmd, self.device.serial)

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
