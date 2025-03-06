# Android模拟器、雷电模拟器的基类
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Sequence

from func_timeout import FunctionTimedOut, func_timeout
from loguru import logger

from androtools.android_sdk import CMD
from androtools.android_sdk.platform_tools import ADB
from androtools.core.constants import Android_API_MAP, KeyEvent


@dataclass
class DeviceInfo:
    """模拟器信息"""

    index: str  # 模拟器序号，雷电模拟器、夜神模拟器的序号
    serial: str | None  # 模拟器序列号，adb -s 的操作对象
    name: str  # 模拟器名称，它可以修改。
    version: int  # 模拟器版本, 如 9 表示 Android 9
    adb_path: str  # adb 路径
    console_path: str  # 模拟器控制器；雷电模拟器则是 ldconsole

    # def __init__(
    #     self,
    #     index: str,
    #     serial: str | None,
    #     name: str,
    #     adb_path: str,
    #     console_path: str,
    # ) -> None:
    #     self.index = index
    #     self.serial = serial
    #     self.name = name
    #     self.adb_path = adb_path
    #     self.console_path = console_path

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

    STOP = "-1"  # 停止
    BOOT = "0"  # 设备启动
    RUN = "1"  # 设备启动完毕，运行中。
    HANG_UP = "2"  # 挂起
    ERORR = "3"  # 模拟器执行 adb 命令没响应，则为错误，需要重启模拟器
    ADB_ERR = "4"  # 模拟器已经启动，但是，adb 找不到设备
    UNKNOWN = "5"  # 未知

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
    def __init__(self, info: DeviceInfo, is_reboot: bool = False) -> None:
        self.info = info
        self._adb_wrapper: ADB = ADB(info.adb_path)
        self._serial = info.serial
        """模拟器序列号，adb -s 的操作对象"""

        try:
            if self.is_boot():
                if is_reboot:
                    func_timeout(60, self.reboot)
            else:
                func_timeout(60, self.launch)
        except FunctionTimedOut:
            self.close()
            raise FunctionTimedOut("启动模拟器超时")

        if self._serial is None:
            self._init_serial()
            self._serial = self.info.serial

        if self._serial is None:
            raise RuntimeError("无法获取设备序列号")

        status = self.get_status()
        if status != DeviceStatus.RUN:
            logger.debug(f"Device {self.info.name} is {status}.")
            self.close()
            raise RuntimeError(f"Device {self.info.name} is not ready.")

        self._init_sdk()
        self.android_version = "Unknown"
        if result := Android_API_MAP.get(self.sdk):
            self.android_version = result[0]

    @property
    def serial(self):
        return self._serial

    def __str__(self) -> str:
        return f"{self.info.name}-{self.android_version}({self.sdk})"

    @abstractmethod
    def _init_serial(self):
        """初始化 self.info.serial"""
        # 通过 adb devices -l
        # console list 获取本设备ID，如果无法获取，则启动设备。
        # 是否已经启动，判断进程ID（PID）是否存在
        # 通过PID获取监听端口集合

        # 通过adb devices -l，获取设备序列号列表
        # 如果在列表里面，则说明完全启动成功。

        # 设备有几种状态：
        # 停止
        # 启动中，存在PID
        # 启动完毕，
        pass

    def _init_sdk(self):
        logger.debug(f"Emu - 初始化模拟器 {self.info.name} SDK")
        output, _ = self.adb_shell(["getprop", "ro.build.version.sdk"])
        if isinstance(output, str):
            self.sdk = int(output)
        elif isinstance(output, list):
            self.sdk = int(output[0])
        return self.sdk

    @abstractmethod
    def is_boot(self) -> bool:
        """判断设备是否已经启动"""
        pass

    def launch(self):
        """启动模拟器"""
        pass

    def close(self):
        """关闭模拟器"""
        pass

    def reboot(self):
        """重启模拟器"""
        pass

    def get_status(self) -> DeviceStatus:
        """获取模拟器状态"""
        return DeviceStatus.UNKNOWN

    def is_crashed(self):
        """判断模拟器是否没响应，如果没响应，则定义为模拟器崩溃"""
        try:
            # 点击HOME键，超过5秒没反应
            func_timeout(5, self.home)
        except FunctionTimedOut:
            return True
        return False

    def install_app(self, apk_path: str):
        """安装apk

        Args:
            apk_path (str): apk 路径

        Returns:
            tuple: (is_success, output)
        """
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

    def adb(self, cmd: list) -> tuple[str, str]:
        """执行 adb 命令"""
        return self._adb_wrapper.run_cmd(cmd, self.info.serial)

    # def adb_shell(self, cmd: list[str], encoding: str | None = None) -> tuple[str, str]:
    def adb_shell(self, cmd: list[str]) -> tuple[str, str]:
        """执行 adb shell 命令"""
        assert cmd is not None
        return self._adb_wrapper.run_shell_cmd(cmd, self.info.serial)

    def adb_shell_daemon(self, cmd: list[str]):
        assert cmd is not None
        assert isinstance(cmd, list)
        self._adb_wrapper.run_shell_cmd_daemon(cmd, self.info.serial)

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
        output, _ = self.adb_shell(["ps"])
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
            self._device_map[dev] = WorkStatus.Free
            if dev.get_status() is not DeviceStatus.RUN:
                dev.launch()

    def add(self, emu: Device):
        if emu.get_status() is not DeviceStatus.RUN:
            emu.launch()
        self._device_map[emu] = WorkStatus.Free

    def remove(self, dev: Device):
        self._devices.remove(dev)
        for device in self._device_map:
            if device.info == dev:
                device.close()
                self._device_map.pop(device)
                break

    def get_total(self) -> int:
        return len(self._device_map)

    def get_free_device(self) -> Device | None:
        for device in self._device_map:
            if self._device_map[device] == WorkStatus.Free:
                self._device_map[device] = WorkStatus.Busy
                logger.debug(f"free device: {device}")
                return device
        return None

    def free_busy_device(self, device: Device):
        if device not in self._device_map:
            return
        self._device_map[device] = WorkStatus.Free
