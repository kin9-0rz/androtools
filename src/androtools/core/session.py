"""Android 目标：真机和模拟器共有的部分，以及模拟器特有的那部分。

    Device              一个 adb 能寻址并接受命令的 Android 目标。真机就是它本身。
    └─ EmulatorSession  加上厂商 Console 和「启动/关闭」这件事
       └─ ConsoleSession

Device 放在这个 module 而不是 device.py，是因为它需要 AndroidShell，而 shell.py
反过来要读 device.py 里的 DeviceInfo —— 放 device.py 会成环。device.py 因此保持
纯词汇表加 Console seam。
"""

import subprocess
import time
from abc import ABC, abstractmethod
from typing import Callable

from func_timeout import FunctionTimedOut, func_timeout

from androtools import logger
from androtools.android_sdk.platform_tools import ADB, AdbRunner, DeviceOfflineError
from androtools.core.constants import KeyEvent
from androtools.core.device import EmulatorConsole, DeviceInfo, DeviceStatus, EmulatorInfo
from androtools.core.shell import AndroidShell

# 状态判定用哪颗按键探测、启动失败重试多少次、崩溃探测等多久 —— 这些都是
# implementation 内部事实，caller 不需要知道。
_PROBE_KEY = KeyEvent.KEYCODE_ALT_LEFT
_BOOT_RETRY_BUDGET = 10
_CRASH_PROBE_TIMEOUT = 5
_VERSION_PROP = "ro.build.version.release"


class Device:
    """一个可以通过 adb 寻址并接受命令的 Android 目标。

    真机（插着 USB 或 WiFi adb 的手机）直接用它，不需要子类，也不需要 Console。
    """

    def __init__(
        self,
        info: DeviceInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """adb 和时间都是依赖，不是自己造出来的。

        Args:
            info: 目标的身份。真机填 DeviceInfo，模拟器填 EmulatorInfo。
            adb: AdbRunner 的 adapter。默认按 info.adb_path 自建真实 ADB；
                传入 FakeADB 即可在没有设备的情况下驱动状态判定。
            sleeper: 等待函数。默认 time.sleep；测试传 lambda _: None 即可
                让重试流程瞬间跑完。
        """
        self.info = info
        self.name = info.name
        # adb runner 同时留给 vendor adapter 用：它们要绕过 DeviceInfo.serial 去
        # 命令别的设备（雷电认实例、MuMu 补 connect），那不是「对 self 操作」。
        self.adb = ADB(info.adb_path) if adb is None else adb
        self.shell = AndroidShell(self.adb, info)
        self._sleep = sleeper
        self.status = DeviceStatus.STOP
        self._android_version: str | None = None

    def __str__(self) -> str:
        """刻意不含 Android 版本 —— 版本是查出来的，取它会发一条 adb 命令，
        而打印对象不该有副作用。多设备场景下 serial 比版本更有用。"""
        return f"{self.name} [{self.info.serial}]"

    @property
    def android_version(self) -> str:
        """设备的 Android 版本，首次访问时才问设备；问不到为 Unknown。

        直接读 ro.build.version.release，而不是查本地的 Android_API_MAP ——
        那张表停在 sdk 35，新机器早就跑上去了（实测一台 sdk 37 的设备，表里没有）。
        设备自己给的答案既权威又不需要维护。
        """
        if self._android_version is None:
            self._android_version = self._probe_version()
        return self._android_version

    def _probe_version(self) -> str:
        try:
            return self.read_prop(_VERSION_PROP) or "Unknown"
        except (DeviceOfflineError, RuntimeError):
            # 问不到不等于出错：设备没启动、掉线、Console 连不上都属于这一类。
            # 只catch 这两种 —— 其它异常照常抛出来，那是真的出错了。
            return "Unknown"

    # ------------------------------------------------------------------------ #
    #                            interface: 状态                                 #
    # ------------------------------------------------------------------------ #

    def get_status(self) -> DeviceStatus:
        """此刻这个目标处于什么状态。

        STOP 对真机的含义是「adb devices 里看不到它」—— 拔线、关 USB 调试、
        未授权，都落在这里。
        """
        if not self.is_boot():
            return DeviceStatus.STOP

        result = self.shell.adb(["get-state"])
        if result.contain("not found"):
            # adb 可能还没认到刚起来的设备，重连一次再问。
            # 只在真的问不到时才重连 —— 实测 `adb reconnect` 会打断 tcp 连接的
            # 模拟器（MuMu）且不会自己恢复，无条件重连等于每次判定都把它弄坏。
            self.reconnect()
            self._sleep(3)
            result = self.shell.adb(["get-state"])
            if result.contain("not found"):
                return DeviceStatus.ERORR

        status = DeviceStatus.BOOT
        if result.output_equal("device"):
            status = DeviceStatus.DEVICE
            try:
                self.shell.input_keyevent(_PROBE_KEY)
            except Exception:
                return DeviceStatus.ERORR
        elif result.contain("offline"):
            logger.debug(f"设备 [{self.name}] 状态: {DeviceStatus.OFFLINE}")
            return DeviceStatus.OFFLINE

        if self.is_boot_completed():
            status = DeviceStatus.BOOT_COMPLETED

        logger.debug(f"设备 [{self.name}] 状态: {status}")
        return status

    def is_boot(self) -> bool:
        """这个目标是否在线。

        默认实现是问 adb 要一次状态。实测：目标不存在时 adb 退出码非 0、**stdout
        为空**，消息（device 'X' not found / no devices/emulators found）都走 stderr。
        所以判据是「adb 有没有报出状态」，而不是去匹配某句错误文案 —— 那样换个
        adb 版本就会漏。

        模拟器有厂商 Console，能不问 adb 就答，覆盖了这个方法。
        """
        return bool(self.shell.adb(["get-state"]).output.strip())

    def is_boot_completed(self) -> bool:
        return self.read_prop("sys.boot_completed") == "1"

    def is_crashed(self) -> bool:
        """目标是否没响应。"""
        try:
            # FIXME - 有模拟器点 home 键会卡死，那时还没有更好的探测方式。
            func_timeout(_CRASH_PROBE_TIMEOUT, self.shell.home)
        except FunctionTimedOut:
            return True
        return False

    def is_timeout(self, seconds: int = 3) -> bool:
        """一条 adb 命令都执行不完，说明已经卡死。"""
        try:
            assert self.info.serial is not None
            self.shell.adb(["shell", "ps"], timeout=seconds)
        except subprocess.TimeoutExpired:
            return True
        return False

    def read_prop(self, prop: str) -> str:
        """读取一个设备属性。厂商 Console 更可靠时，模拟器会覆盖这个方法。"""
        return self.shell.getprop(prop)

    # ------------------------------------------------------------------------ #
    #                            interface: 应用操作                              #
    # ------------------------------------------------------------------------ #

    def run_app(self, package: str) -> bool:
        """启动应用的主界面。厂商 Console 更可靠时，模拟器会覆盖它。"""
        return self.shell.run_app(package)

    def kill_app(self, package: str) -> None:
        self.shell.kill_app(package)

    # ------------------------------------------------------------------------ #
    #                             implementation                                 #
    # ------------------------------------------------------------------------ #

    def reconnect(self) -> None:
        self.shell.adb(["reconnect"])


class EmulatorSession(Device, ABC):
    """一个由厂商应用托管的模拟器。

    起来没有、属性读取、应用的启停都走 Console —— 在开机阶段 adb 还连不上。
    真机没有这一层，也不需要 launch / close。
    """

    def __init__(
        self,
        info: EmulatorInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(info, adb=adb, sleeper=sleeper)
        self.index = info.index

    def ensure_ready(self) -> bool:
        """启动模拟器并等到开机完成。失败时关掉模拟器并返回 False。"""
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

    # ------------------------------------------------------------------------ #
    #                        interface: 厂商必须实现                              #
    # ------------------------------------------------------------------------ #

    @abstractmethod
    def launch(self):
        """启动模拟器"""

    @abstractmethod
    def close(self):
        """关闭模拟器"""

    @abstractmethod
    def is_boot(self) -> bool:
        """模拟器进程是否已经起来了。

        依据随厂商而变（进程 PID、状态字符串、窗口句柄），所以这是子类的义务。
        """


class ConsoleSession(EmulatorSession):
    """由厂商 Console 驱动的会话。

    新增一个厂商只需要写两个 adapter：一个 Console 实现 EmulatorConsole，一个
    Player 实现本类。已有的代码一行都不用改。
    """

    def __init__(
        self,
        info: EmulatorInfo,
        console: EmulatorConsole,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(info, adb=adb, sleeper=sleeper)
        self.console = console

    def is_boot(self) -> bool:
        return self.console.probe_state(self.index) == DeviceStatus.BOOT

    def read_prop(self, prop: str) -> str:
        """通过 Console 读属性。

    先确认模拟器起来了 —— 因为厂商 CLI 在实例没启动时不会干净地失败：雷电的
    ldconsole getprop 会把 adb 的报错写进 **stdout 并返回 exit 0**，调用方没法从
    返回码或 stderr 判断，只能拿到一句错误文案当属性值。拦在这里比事后在每个
    调用方判断要可靠得多。
    """
        if self.console.probe_state(self.index) != DeviceStatus.BOOT:
            raise RuntimeError(f"{self.name} 未启动，Console 问不到属性 {prop}")
        return self.console.getprop(self.index, prop)

    def run_app(self, package: str) -> bool:
        return self.console.run_app(self.index, package)

    def kill_app(self, package: str) -> None:
        self.console.kill_app(self.index, package)
