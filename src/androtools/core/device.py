from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

from androtools.cmd import CMD
from androtools.cmd.result import CmdResult


class Pids(NamedTuple):
    """一台模拟器的两个进程标识。

    界面进程和 VM 进程是两个不同的进程，只看其中一个不足以判断模拟器是否
    完全启动。这是 console 内部的类型 —— interface 上暴露的是状态，不是 PID。
    不是每个厂商都给得出两个（MuMu 的 CLI 只给一个），所以它不在 interface 上。
    """

    ui: int
    """界面进程的 PID，雷电是 dnplayer.exe"""
    vm: int
    """VM 进程的 PID，雷电是 VBox；负责与 adb 通信"""

    def is_running(self) -> bool:
        """两个进程都在才算完全启动。只看界面进程会把「半启动」误判成已启动。"""
        return self.ui != -1 and self.vm != -1


@dataclass
class DeviceInfo:
    """一个 adb 目标的身份。真机和模拟器共有的部分。"""

    name: str  # 展示用的名字
    serial: str | None  # adb -s 的操作对象
    adb_path: str  # adb 路径

    def __eq__(self, other: object) -> bool:
        """身份是 serial + adb_path。

        不比 name —— 模拟器的名字用户可以改。也不比 index / console_path：
        那是模拟器的属性，不是这台设备的身份。

        serial 本身要参与判定，因为它是 adb 唯一的寻址依据。注意 MuMu 会在启动
        流程里改写它，所以改写之后是两个不同的对象 —— 想要「同一台设备的前后
        身份」，用 serial 之外的键自己维护。
        """
        if not isinstance(other, DeviceInfo):
            return NotImplemented

        return self.serial == other.serial and self.adb_path == other.adb_path

    def __repr__(self) -> str:
        return f"{self.name} ({self.serial})"


@dataclass(eq=False)
class EmulatorInfo(DeviceInfo):
    """模拟器的身份：厂商 Console 的编号，加上它自带的控制台路径。

    真机没有这两样东西，所以它们不在 DeviceInfo 里。
    """

    index: str  # Index —— 模拟器在厂商 Console 里的稳定编号，跨重启不变
    console_path: str  # 厂商控制台可执行文件的路径，如 ldconsole.exe

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
    ERORR = "4"  # 异常状态，设备已经启动，但是，无法交互等等。只能重新启动

    @staticmethod
    def get(value: str):
        for item in DeviceStatus:
            if item.value == value:
                return item
        raise Exception("未知状态")


class EmulatorConsole(CMD, ABC):
    """模拟器控制台：启动、关闭、重启模拟器，探测它起来没有，以及属性和应用操作。

    厂商的命令行方言（雷电的 `--index N`、MuMu 的 `-v N` 加 JSON 输出）
    止步于这个 interface 后面 —— session 不应该知道这些。

    关键设计：console 只回答「起来了吗」这个粗问题（probe_state），由 session
    再用 adb 细分出 DEVICE / BOOT_COMPLETED / OFFLINE / ERORR。厂商的信号强度
    差别很大 —— 雷电要靠两个进程 PID 推断，MuMu 直接给进程状态
    —— 所以 interface 上暴露的是状态，不是 PID。Pids 只在具体 console 内部使用。

    这里只放 session 真正需要的能力。厂商特有的（locate、setprop、install_app 等）
    留在具体 console 上，不进 interface。
    """

    @abstractmethod
    def launch_device(self, idx: int | str) -> CmdResult | None:
        """启动模拟器"""

    @abstractmethod
    def reboot_device(self, idx: int | str) -> CmdResult | None:
        """重启模拟器"""

    @abstractmethod
    def quit_device(self, idx: int | str) -> CmdResult | None:
        """关闭模拟器"""

    @abstractmethod
    def probe_state(self, idx: int | str) -> DeviceStatus:
        """模拟器起来了没有。

        只能粗略回答：最多区分 STOP（完全没起来）和 BOOT（进程在了）。开机是否
        完成、是否离线，由 session 用 adb 继续判定。
        """

    @abstractmethod
    def getprop(self, idx: int | str, prop: str | None) -> str:
        """读取模拟器上的属性。模拟器开机阶段 adb 拿不到，只能走这里。"""

    @abstractmethod
    def run_app(self, idx: int | str, package: str) -> bool:
        """通过 Console 启动应用。"""

    @abstractmethod
    def kill_app(self, idx: int | str, package: str) -> None:
        """通过 Console 杀死应用。"""

    def instances(self) -> list[str]:
        """这个厂商当前存在的实例编号，按 list 顺序。

        用于「反查 adb 上的某个 serial 是谁」—— 没有它就只能对每个 index
        从 0 往上试，而 index 段里可能有空洞。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 instances()")

    def fingerprint(self, idx: int | str) -> str | None:
        """这个实例在 adb 上可被认出来的唯一值；拿不出就返回 None。

        它必须满足两个条件，缺一不可：

        1. **两侧都能读到** —— 一侧是本厂商的权威来源（配置文件），另一侧是
           guest 里 adb 能问到的字段。雷电的 MAC 两边都有：写在实例配置的
           `propertySettings.macAddress` 里，也注入了 guest 的 wlan0。
        2. **每实例不同** —— 雷电实例都是克隆的，ro.product.model 之类在实例间
           必然相同（实测两台雷电的 ro.product.device 都是 marlin）。

        返回 None 的厂商会被 `identify` 跳过。**不能拿 None 当期望值去比对** ——
        那会让任何读不出指纹的设备都「匹配上」。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 fingerprint()")
