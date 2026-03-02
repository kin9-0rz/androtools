import subprocess
import time
from enum import Enum

from androtools import logger
from androtools.control.delegate import (
    AppDelegate,
    LinuxCommand,
    ProxyDelegate,
    TouchController,
)
from androtools.control.delegate.env import EnvDelegate
from androtools.device.abc import Device, DeviceADB
from androtools.device.constants import KeyEvent


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
    ERORR = "4"  # 异常状态，设备已经启动，但是，无法交互等等。只能重新启动

    @staticmethod
    def get(value: str):
        for item in DeviceStatus:
            if item.value == value:
                return item
        raise Exception("未知状态")


class DeviceController:
    """设备控制器

    直接绑定设备。
    设备状态，模拟器内存占用等等。

    1. 设备操作，启动、重启、关闭。
    2. 代理配置
    3. ADB操作
    4. Linux 命令操作
    5. 应用操作
    6. 截图
    """

    def __init__(self, device: Device) -> None:
        self.device = device
        self.adb = DeviceADB(self.device)
        self.app = AppDelegate(self.adb)
        self.proxy = ProxyDelegate(self.adb)
        self.touch = TouchController(self.adb)
        self.linux_command = LinuxCommand(self.adb)
        self.env = EnvDelegate(self.adb)

    def screencap(self, save_dir: str, filename: str):
        """截图，并保存到指定目录

        Args:
            save_dir (str): 图片存放目录
            filename (str): 图片名
        """
        self.device.adb.run_shell_cmd(["mkdir", "-p", save_dir])
        output = save_dir + "/" + filename
        cmd = ["screencap", output]
        self.device.adb.run_shell_cmd(cmd)

    def is_timeout(self, seconds: int = 3):
        """判断模拟器是否超时，命令执行超时，说明模拟器已经卡死，需要重启"""
        try:
            assert self.device.info.serial is not None
            # 执行 adb 命令，不显示输出， 设置 3 秒超时
            subprocess.run(
                [
                    self.device.info.adb_path,
                    "-s",
                    self.device.info.serial,
                    "shell",
                    "ps",
                ],
                timeout=seconds,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return True
        return False

    def get_status(self):
        status = DeviceStatus.STOP
        if self.device.is_boot():
            status = DeviceStatus.BOOT
        else:
            return status  # 模拟器未启动

        is_tried = False
        while True:
            self.device.adb.run_cmd(["get-state"])
            result = self.adb.adb(["get-state"])
            if result.contain("not found"):
                # error: device 'emulator-5556' not found
                if not is_tried:
                    is_tried = True
                    # NOTE: adb 执行的速度太快可能会导致 not found
                    self.device.reconnect()
                    time.sleep(5)
                    continue
                status = DeviceStatus.ERORR
                return status

            if result.output_equal("device"):
                status = DeviceStatus.DEVICE
                # TODO: 可以尝试使用 am 相关命令做测试
                # self.adb_shell(["ps"])
                is_ok = self.touch.input_keyevent(KeyEvent.KEYCODE_ALT_LEFT)
                if not is_ok:
                    logger.warning("执行命令超时！")
                    status = DeviceStatus.ERORR
                    return status
            elif result.error_contain("offline"):
                status = DeviceStatus.OFFLINE
                logger.debug(f"设备 [{self.device.name}] 状态: {status}")
                return status
            break

        if self.device.is_boot_completed():
            status = DeviceStatus.BOOT_COMPLETED

        logger.debug(f"设备 [{self.device.name}] 状态: {status}")
        return status
