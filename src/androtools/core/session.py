"""模拟器生命周期。

这个 module 是 deep 的：状态判定、启动等待、崩溃恢复的复杂度都在 implementation
后面，caller 只需要学会四个方法 —— get_status / ensure_ready / recover / read_prop。
轮询间隔、重试次数、sleep 时长、magic timeout 全是 implementation 内部事实。

Console 的命令行方言停在 DeviceConsole 后面，adb 的调用停在 AdbRunner 后面，
这两个 seam 让这里可以被 FakeADB 和注入的 console 完整驱动，不需要真模拟器。
"""

import subprocess
import time
from abc import ABC, abstractmethod
from typing import Callable

from func_timeout import FunctionTimedOut, func_timeout

from androtools import logger
from androtools.android_sdk.platform_tools import ADB, AdbRunner
from androtools.core.constants import KeyEvent
from androtools.core.device import DeviceConsole, DeviceInfo, DeviceStatus
from androtools.core.shell import AndroidShell

# 状态判定用哪颗按键探测、启动失败重试多少次、崩溃探测等多久 —— 这些都是
# implementation 内部事实，caller 不需要知道。
_PROBE_KEY = KeyEvent.KEYCODE_ALT_LEFT
_BOOT_RETRY_BUDGET = 10
_CRASH_PROBE_TIMEOUT = 5


class EmulatorSession(ABC):
    """一台模拟器从启动到可交互的全过程。"""

    def __init__(
        self,
        info: DeviceInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """adb 和时间都是依赖，不是自己造出来的。

        Args:
            info: Device 的身份信息。
            adb: AdbRunner 的 adapter。默认按 info.adb_path 自建真实 ADB；
                传入 FakeADB 即可在没有模拟器的情况下驱动生命周期逻辑。
            sleeper: 等待函数。默认 time.sleep；测试传 lambda _: None 即可
                让启动/重试流程瞬间跑完。
        """
        self.info = info
        self.name = info.name
        self.shell = AndroidShell(ADB(info.adb_path) if adb is None else adb, info)
        self._sleep = sleeper
        self.android_version: int | str = info.version
        self.status = DeviceStatus.STOP

    def __str__(self) -> str:
        return f"{self.info.name}-{self.android_version}"

    # ------------------------------------------------------------------------ #
    #                          interface: 生命周期                                #
    # ------------------------------------------------------------------------ #

    def get_status(self) -> DeviceStatus:
        """此刻模拟器处于什么状态。"""
        status = DeviceStatus.STOP
        if not self.is_boot():
            return status  # 模拟器未启动
        status = DeviceStatus.BOOT

        # 刷新模拟器的状态
        self.reconnect()
        self._sleep(3)

        result = self.shell.adb(["get-state"])
        if result.contain("not found"):
            # NOTE: adb 执行的速度太快可能会导致 not found
            # error: device 'emulator-5556' not found
            # 再次确认
            self.reconnect()
            self._sleep(5)
            result = self.shell.adb(["get-state"])
            if result.contain("not found"):
                return DeviceStatus.ERORR

        if result.output_equal("device"):
            status = DeviceStatus.DEVICE
            try:
                self.shell.input_keyevent(_PROBE_KEY)
            except Exception:
                return DeviceStatus.ERORR
        elif result.error_contain("offline"):
            logger.debug(f"设备 [{self.name}] 状态: {DeviceStatus.OFFLINE}")
            return DeviceStatus.OFFLINE

        if self.is_boot_completed():
            status = DeviceStatus.BOOT_COMPLETED

        logger.debug(f"设备 [{self.name}] 状态: {status}")
        return status

    def ensure_ready(self) -> bool:
        """启动模拟器并等到开机完成。失败时关掉模拟器并返回 False。

        1. 判断设备是否已经启动，如果没有启动，则启动设备。
        2. 启动后，则等待设备连接，连接成功，则尝试获取设备是否已经启动。
        """
        if not self.is_boot():
            logger.debug(f"启动设备 {self.name}")
            self.launch()
            self._sleep(30)

            while True:
                self._sleep(5)
                if self.is_boot():
                    break

        status = self.get_status()
        if status == DeviceStatus.BOOT_COMPLETED:
            logger.debug(f"设备 {self.name} 已经启动")
            self.status = status
            return True

        counter = 0
        while True:
            counter += 1
            self._sleep(6)

            status = self.get_status()
            if status == DeviceStatus.BOOT_COMPLETED:
                self.status = status
                break

            if counter > _BOOT_RETRY_BUDGET:
                logger.warning(f"设备 {self.name} 启动失败！")
                self.close()
                self.status = DeviceStatus.STOP
                return False

        return True

    def recover(self) -> DeviceStatus:
        """模拟器卡死时的恢复动作：重启，返回重启后的状态。"""
        self.close()
        self._sleep(10)
        self.launch()
        return self.get_status()

    def read_prop(self, prop: str) -> str:
        """读取一个设备属性。

        默认走 adb。厂商 Console 的 getprop 更可靠时，覆盖这个方法。
        """
        return self.shell.getprop(prop)

    # ------------------------------------------------------------------------ #
    #                          interface: 健康检查                                #
    # ------------------------------------------------------------------------ #

    @abstractmethod
    def is_boot(self) -> bool:
        """模拟器进程是否已经起来了。

        每个厂商的判断依据不同（进程 PID、状态字符串、窗口句柄），所以这是
        子类的义务，基类不给实现。"""

    def is_boot_completed(self) -> bool:
        return self.read_prop("sys.boot_completed") == "1"

    def is_crashed(self) -> bool:
        """模拟器是否没响应。"""
        try:
            # FIXME - 夜神模拟器存在点击home按键卡死。
            func_timeout(_CRASH_PROBE_TIMEOUT, self.shell.home)
        except FunctionTimedOut:
            return True
        return False

    def is_timeout(self, seconds: int = 3) -> bool:
        """一条 adb 命令都执行不完，说明模拟器已经卡死。"""
        try:
            assert self.info.serial is not None
            self.shell.adb(["shell", "ps"], timeout=seconds)
        except subprocess.TimeoutExpired:
            return True
        return False

    # ------------------------------------------------------------------------ #
    #                        interface: 应用管理（默认走 adb）                     #
    # ------------------------------------------------------------------------ #

    def run_app(self, package: str) -> bool:
        """启动应用的主界面。厂商 Console 更可靠时，ConsoleSession 会覆盖它。"""
        return self.shell.run_app(package)

    def kill_app(self, package: str) -> None:
        self.shell.kill_app(package)

    # ------------------------------------------------------------------------ #
    #                         interface: 厂商必须实现                              #
    # ------------------------------------------------------------------------ #

    @abstractmethod
    def launch(self):
        """启动模拟器"""

    @abstractmethod
    def close(self):
        """关闭模拟器"""

    # ------------------------------------------------------------------------ #
    #                              implementation                                 #
    # ------------------------------------------------------------------------ #

    def reconnect(self) -> None:
        self.shell.adb(["reconnect"])


class ConsoleSession(EmulatorSession):
    """由厂商 Console 驱动的会话。

    起来没有、属性读取、应用的启停都走 Console —— 雷电、夜神、MuMu 在开机阶段
    adb 都还连不上，走 adb 拿不到属性、也起不了应用。新增一个厂商只需要写
    MumuConsole(DeviceConsole) 和 MumuPlayer(ConsoleSession) 两个 adapter。
    """

    def __init__(
        self,
        info: DeviceInfo,
        console: DeviceConsole,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(info, adb=adb, sleeper=sleeper)
        self.index = info.index
        self.console = console

    def is_boot(self) -> bool:
        return self.console.probe_state(self.index) == DeviceStatus.BOOT

    def read_prop(self, prop: str) -> str:
        return self.console.getprop(self.index, prop)

    def run_app(self, package: str) -> bool:
        return self.console.run_app(self.index, package)

    def kill_app(self, package: str) -> None:
        self.console.kill_app(self.index, package)