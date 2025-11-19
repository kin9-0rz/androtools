# Android模拟器、雷电模拟器的基类
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Sequence
import time
import subprocess

from func_timeout import FunctionTimedOut, func_timeout
from androtools import logger

from androtools.android_sdk import CMD
from androtools.android_sdk.platform_tools import ADB
from androtools.core.constants import Android_API_MAP, KeyEvent


class DeviceType(Enum):
    """模拟器类型"""

    LD = "ld"
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

    device_type: DeviceType
    index: str  # 模拟器序号，雷电模拟器、夜神模拟器的序号
    serial: str | None  # 模拟器序列号，adb -s 的操作对象
    name: str  # 模拟器名称，它可以修改。
    version: int  # 模拟器版本, 如 9 表示 Android 9
    adb_path: str  # adb 路径
    console_path: str  # 模拟器控制器；雷电模拟器则是 ldconsole
    gateway: str  # 网关IP
    proxy_port: int  # mitmproxy 代理端口

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


class DeviceStatus(Enum):
    """模拟器状态"""

    STOP = "-1"  # 停止，设备还没启动
    BOOT = "0"  # 1. 设备启动，存在PID
    DEVICE = "1"  # 设备已连接
    BOOT_COMPLETED = "2"  # 2. 设备启动完毕
    OFFLINE = "3"
    """
    设备离线

    1、启动过程中，等待启动完毕。<br>
    2、启动完毕，adb 无法操作，只能重启。
    """
    ERORR = "4"  # 模拟器执行 adb 命令没响应，则为错误，需要重启模拟器
    UNKNOWN = "5"  # 未知
    RUNNING = "6"  # 设备正在运行

    @staticmethod
    def get(value: str):
        for item in DeviceStatus:
            if item.value == value:
                return item
        return DeviceStatus.UNKNOWN


class WorkStatus(Enum):
    Free = 0
    Busy = 1


class DeviceConsole(CMD):
    """模拟器控制台，用于控制模拟器的启动和关闭。"""

    def launch_device(self, idx: int | str):
        """启动模拟器"""
        pass

    def reboot_device(self, idx: int | str):
        """重启模拟器"""
        pass

    def quit_device(self, idx: int | str):
        """关闭模拟器"""
        pass

    def quit_all_devices(self):
        """关闭所有的模拟器"""
        pass

    @abstractmethod
    def list_devices(self) -> str:
        """列出所有模拟器信息"""
        pass


class Device(ABC):
    # FIXME 定义接口，不要具体的实现
    def __init__(self, info: DeviceInfo) -> None:
        self.info = info
        self.name = info.name
        self._adb_wrapper: ADB = ADB(info.adb_path)
        self.android_version = info.version
        self.sdk = None
        self.status = DeviceStatus.STOP
        self._is_busy = False
        self.pid = -1

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @is_busy.setter
    def is_busy(self, value: bool) -> None:
        self._is_busy = value

    @property
    def adb_wrapper(self) -> ADB:
        return self._adb_wrapper

    def get_android_version(self):
        if self.sdk is None:
            self.sdk = self.get_sdk()

        self.android_version = "Unknown"
        if result := Android_API_MAP.get(self.sdk):
            self.android_version = result[0]

    def __str__(self) -> str:
        return f"{self.info.name}-{self.android_version}"

    @abstractmethod
    def is_boot(self) -> bool:
        """判断设备是否已经启动"""

    def is_boot_completed(self) -> bool:
        r = self.getprop("sys.boot_completed")
        return r == "1"

    def is_crashed(self):
        """判断模拟器是否没响应，如果没响应，则定义为模拟器崩溃"""
        try:
            # FIXME - 夜神模拟器存在点击home按键卡死。
            func_timeout(5, self.home)
        except FunctionTimedOut:
            return True
        return False

    def is_timeout(self, seconds: int = 3):
        """判断模拟器是否超时，命令执行超时，说明模拟器已经卡死，需要重启"""
        try:
            assert self.info.serial is not None
            # 执行 adb 命令，不显示输出， 设置 3 秒超时
            subprocess.run(
                [self.info.adb_path, "-s", self.info.serial, "shell", "ps"],
                timeout=seconds,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return True
        return False

    def launch_and_wait_for_device(self):
        """设备第一次，必须要确认是否已经启动
        1. 判断设备是否已经启动，如果没有启动，则启动设备。

        """
        status = self.get_status()
        if status == DeviceStatus.BOOT_COMPLETED:
            logger.debug(f"设备 {self.name} 已经启动")
            self.status = status
            return True

        logger.debug(f"设备 {self.name} 尝试启动")
        if self.status == DeviceStatus.STOP:
            self.launch()

            while True:
                time.sleep(5)
                if self.is_boot():
                    break

            counter = 0
            while True:
                counter += 1
                status = self.get_status()
                if status == DeviceStatus.BOOT_COMPLETED:
                    self.status = DeviceStatus.RUNNING
                    break
                time.sleep(6)

                # NOTE: 1分钟
                if counter > 10:
                    logger.warning(f"设备 {self.name} 启动超时, 建议重新启动")
                    self.close()
                    self.status = DeviceStatus.STOP
                    return False
        return True

    def reconnect(self):
        self.adb(["reconnect"])

    # 获取此时此刻模拟器的状态
    def get_status(self):
        status = DeviceStatus.STOP
        if self.is_boot():
            status = DeviceStatus.BOOT
        else:
            return status  # 模拟器未启动

        # 刷新模拟器的状态
        self.reconnect()
        self.reconnect()
        self.reconnect()

        # 如果设备还没启动，就会这样子
        # adb.exe -s emulator-5556 get-state
        # error: device 'emulator-5556' not found
        out, err = self.adb(["get-state"])
        if "device" in out:
            status = DeviceStatus.DEVICE
        elif "offline" in err:
            status = DeviceStatus.OFFLINE
            logger.debug(f"设备 [{self.name}] 状态: {status}")
            return status

        if self.is_boot_completed():
            status = DeviceStatus.BOOT_COMPLETED

        logger.debug(f"设备 [{self.name}] 状态: {status}")
        return status

    def launch(self):
        """启动模拟器"""
        pass

    def close(self):
        """关闭模拟器"""
        pass

    @abstractmethod
    def reboot(self) -> DeviceStatus:
        """重启模拟器"""
        pass

    def adb(self, cmd: list) -> tuple[str, str]:
        """执行 adb 命令"""
        return self._adb_wrapper.run_cmd(cmd, self.info.serial)

    def adb_shell(self, cmd: list[str]) -> tuple[str, str]:
        """执行 adb shell 命令"""
        assert cmd is not None
        assert isinstance(cmd, list)
        return self._adb_wrapper.run_shell_cmd(cmd, self.info.serial)

    def adb_shell_daemon(self, cmd: list[str]):
        assert cmd is not None
        assert isinstance(cmd, list)
        if isinstance(cmd, str):
            raise TypeError(f"命令必须是列表：{cmd}")
        self._adb_wrapper.run_shell_cmd_daemon(cmd, self.info.serial)

    def getprop(self, prop: str | None = None) -> str:
        """获取模拟器属性"""
        if prop:
            output, _ = self.adb_shell(["getprop", prop])
        else:
            output, _ = self.adb(["getprop"])

        return output.strip()

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

    def install_app(self, apk_path: str):
        """安装apk

        Args:
            apk_path (str): apk 路径

        Returns:
            tuple: (is_success, output)
        """
        if self.sdk is None:
            self.sdk = self.get_sdk()

        cmd = ["install", "-r", "-g", "-t", apk_path]
        if self.sdk < 25:
            cmd = ["install", "-r", "-t", apk_path]
        output, errout = self.adb(cmd)

        if "error" in output:
            logger.error(" ".join(cmd))
            logger.error(output)
            return False, output

        return "Success" in errout, output + " | " + errout.strip()

    def uninstall_app(self, package_name: str):
        """卸载应用"""
        cmd = ["uninstall", package_name]
        output, error = self.adb(cmd)
        if "Success" in output:
            return True
        logger.error(" ".join(cmd))
        logger.error(output)
        logger.error(error, stack_info=True)

    def grant_permission(self, package_name: str, permission: str):
        self.adb_shell(["pm", "grant", package_name, permission])

    def grant_all_permissions(self, package_name: str):
        r = self.adb_shell(["pm", "dump", package_name, "|", "grep", "granted=false"])
        for line in r[0].split("\n"):
            line = line.strip()
            if line == "":
                continue
            if "granted=false" not in line:
                continue
            perm = line.split(":")[0]
            self.grant_permission(package_name, perm)

    def run_app(self, package: str) -> bool:
        """启动一个应用

        Args:
            package (str): 应用包名

        Returns:
            bool: 如果返回True，表示存在主界面；如果返回False，表示不存在主界面
        """
        out, _ = self.adb_shell(["dumpsys", "package", package])
        out = out.strip()

        activity_start = out.find("android.intent.action.MAIN:")
        if activity_start == -1:
            return False

        # android.intent.action.MAIN:\n\n，从这个字符串的尾部开始找
        activity_start += 31
        activity_end = out.find("\n", activity_start)

        activity_name = None
        for item in out[activity_start:activity_end].strip().split():
            if "/" in item:
                activity_name = item
                break
        if activity_name is None:
            return False

        cmd = ["am", "start", "-n", f"{activity_name}"]
        self.adb_shell(cmd)
        return True

    def kill_app(self, package: str):
        self.adb_shell(["am", "force-stop", package])

    def pull(self, remote: str, local: str):
        """将文件从模拟器下载到本地"""
        cmd = ["pull", remote, local]
        output, error = self.adb(cmd)
        if "pulled" in output:
            return True
        logger.error(" ".join(cmd))
        if output:
            logger.error(output)
        if error:
            logger.error(error)

    def push(self, local: str, remote: str):
        """将文件从本地上传到模拟器"""
        self.adb(["push", local, remote])

    def rm(self, path: str, isDir: bool = False, force: bool = False):
        """删除文件

        Args:
            path (str): 文件路径
            force (bool, optional): 是否强制删除，默认否. Defaults to False.
        """
        cmd = ["rm"]
        if isDir:
            cmd.append("-r")
        if force:
            cmd.append("-f")
        cmd.append(path)
        self.adb_shell(cmd)

    def ls(self, path: str):
        cmd = ["ls", path]
        output, err = self.adb_shell(cmd)
        if err is not None:
            return err
        return output

    def mkdir(self, path):
        self.adb_shell(["mkdir", path])

    def ps(self):
        output, _ = self.adb_shell(["ps", "-A"])
        return output

    def pidof(self, process_name):
        output, _ = self.adb_shell(["pidof", process_name])
        output = output.strip()
        if "pidof: not found" in output:
            output, _ = self.adb_shell(["ps"])
            lines = output.splitlines()
            for line in lines:
                parts = line.split()
                if parts[-1] == process_name:
                    return int(parts[1])
            return
        return None if output == "" else int(output)

    def killall(self, process_name):
        output, _ = self.adb_shell(["killall", process_name])
        return output

    def kill(self, pid):
        cmd = ["kill", str(pid)]
        self.adb_shell(cmd)

    def dumpsys_window_windows(self):
        cmd = ["dumpsys", "window", "windows"]
        output, _ = self.adb_shell(cmd)
        return output

    def tap(self, x: int, y: int):
        cmd = ["input", "tap", str(x), str(y)]
        self.adb_shell(cmd)

    def long_press(self, x: int, y: int):
        self.swipe(x, y, x, y, 750)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, time: int | None = None):
        cmd = ["input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        if time:
            cmd.append(str(time))
        self.adb_shell(cmd)

    def input_keyevent(self, keyevent: KeyEvent):
        cmd = ["input", "keyevent", str(keyevent.value)]
        self.adb_shell(cmd)

    def input_text(self, txt: str):
        cmd = ["input", "text", txt]
        self.adb_shell(cmd)

    def home(self):
        self.input_keyevent(KeyEvent.KEYCODE_HOME)

    def back(self):
        self.input_keyevent(KeyEvent.KEYCODE_BACK)

    def delete(self):
        self.input_keyevent(KeyEvent.KEYCODE_DEL)

    # ---------------------------------------------------------------------------- #
    #                             截图、dump等Android相关的命令                       #
    # ---------------------------------------------------------------------------- #
    def list_packages(self, flag: Literal[-1, 0, 1] = -1) -> list[str]:
        """列出设备的应用列表

        Args:
            flag (Literal[-1, 0, 1], 可选): `0` 表示第三方应用，`1` 表示系统应用，`-1` 表示所有的应用；默认 `-1`。

        Returns:
            list[str]: 包名列表
        """
        cmd = ["pm", "list", "packages"]
        if flag == 0:
            cmd.append("-3")
        elif flag == 1:
            cmd.append("-s")
        output, _ = self.adb_shell(cmd)
        return output.strip().replace("package:", "").split()

    def screencap(self, save_dir: str, filename: str):
        """截图，并保存到指定目录

        Args:
            save_dir (str): 图片存放目录
            filename (str): 图片名
        """
        self.adb_shell(["mkdir", "-p", save_dir])
        output = save_dir + "/" + filename
        cmd = ["screencap", output]
        self.adb_shell(cmd)


class DeviceManager:
    """
    只能管理 Android 同版的模拟器，不同版本，无法执行 adb。
    1. 根据已知设备初始化。
    2. 增加设备。
    3. 删除设备。
    """

    # 传入的不应该是信息？而是一个具体模拟器对象
    def __init__(self, devices: Sequence[Device]):
        self._devices: list[Device] = list(devices)
        self._device_map: dict[Device, WorkStatus] = {}
        self._device_map.clear()
        for dev in devices:
            logger.info(f"初始化设备 {dev.name}")
            dev.launch_and_wait_for_device()

    def add(self, dev: Device):
        dev.launch_and_wait_for_device()

    def remove(self, dev: Device):
        self._devices.remove(dev)

    def get_total(self) -> int:
        return len(self._devices)

    def get_free_device(self) -> Device | None:
        for dev in self._devices:
            if dev.is_busy:
                continue
            return dev
        return None

    def free_busy_device(self, device: Device):
        if device not in self._device_map:
            return
        self._device_map[device] = WorkStatus.Free
