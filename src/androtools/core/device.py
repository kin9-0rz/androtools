from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from androtools.cmd import CMD
from androtools.cmd.result import CmdResult


def bundled_adb_path(console_path: str | None) -> str:
    """厂商自带的那一份 adb —— 就在它自己的 Console 旁边。

    用厂商配套的 adb，是为了和它自己的 Console 版本对上。目录布局换了或路径
    拿不到时退回 PATH 上的 adb，不抛异常：这一层不该因为一个路径判断失败而
    让整个设备不可用。
    """
    if console_path:
        sibling = Path(console_path).parent / "adb.exe"
        if sibling.is_file():
            return str(sibling)
    return "adb"


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


@dataclass(frozen=True, eq=False)
class EmulatorInstance:
    """某一刻观测到的一台模拟器实例：身份 + 运行状态 + Android 版本。

    与 EmulatorInfo 的分工：EmulatorInfo 是**身份**，是构造一个 session 所需的
    输入，字段稳定；EmulatorInstance 是**观测快照**，status 和 android_version
    随实例运行而变化，所以它是 frozen 的 —— 要新的状态就重新 list 一次，而不是
    在这个对象上改。

    它是「起点」：调用方先 `list_instances()` 拿到有哪些实例、哪台在跑，再拿其中
    一个去 `play()` 成可操作的 session。
    """

    info: EmulatorInfo
    status: DeviceStatus
    android_version: str | None = None
    """Android 版本，如 "12"。拿不到就是 None（雷电的 CLI 不报它）。"""

    def _identity(self) -> tuple:
        return (self.info.index, self.info.console_path, self.status, self.android_version)

    def __eq__(self, other: object) -> bool:
        """相等 = 同一台实例（index + console_path）处在同一个观测状态。

        不比 serial：MuMu 重启后 adb 端口会变，那不该让「同一台实例」变成两台。
        也不整个比 EmulatorInfo —— 它的 __eq__ 把 serial 算进去了。
        """
        if not isinstance(other, EmulatorInstance):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    @property
    def index(self) -> str:
        return self.info.index

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def serial(self) -> str | None:
        return self.info.serial

    @property
    def is_running(self) -> bool:
        """进程在不在。注意它不回答「能不能 adb」—— 那要 session 细分。"""
        return self.status is not DeviceStatus.STOP

    def __repr__(self) -> str:
        return f"{self.info!r} [{self.status.name}]"


class EmulatorConsole(CMD, ABC):
    """模拟器控制台：启动、关闭、重启模拟器，探测它起来没有，以及属性和应用操作。

    厂商的命令行方言（雷电的 `--index N`、MuMu 的 `-v N` 加 JSON 输出）
    止步于这个 interface 后面 —— session 不应该知道这些。

    关键设计：console 只回答「起来了吗」这个粗问题（probe_state），由 session
    再用 adb 细分出 DEVICE / BOOT_COMPLETED / OFFLINE / ERORR。厂商的信号强度
    差别很大 —— 雷电要靠两个进程 PID 推断，MuMu 直接给进程状态
    —— 所以 interface 上暴露的是状态，不是 PID。Pids 只在具体 console 内部使用。

    这里只放 session 真正需要的能力，加上「起点」——列出有哪些实例。厂商特有的
    （locate、setprop、install_app 等）留在具体 console 上，不进 interface。
    厂商给不出来的能力一律是**软成员**（默认抛 NotImplementedError），不是
    abstract：第三方实现这个 interface 时才不会因为新加一个方法就 TypeError。
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

    def list_instances(self) -> list[EmulatorInstance]:
        """这个厂商现在有哪些实例 —— **包含没在跑的那些**。

        与 instances() 的区别：instances() 只给编号，是反查流程的内部需要；
        list_instances() 给带运行状态与版本的完整快照，是使用者操作的**起点**。
        它不该依赖 adb —— 一台纯 Console 调用就该回答「有几台、哪台在跑」。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 list_instances()")

    def create(self, name: str | None = None) -> str:
        """新建一台实例，返回它的 index。

        拿不到新 index 时必须抛异常，**不能**返回一个猜的值 —— 调用方拿着它
        去 launch 就会启动错的机器。名字为 None 时用厂商的默认名。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 create()")

    def clone(self, idx: int | str, name: str | None = None) -> str:
        """基于现有实例复制一台，返回新 index 的字符串。

        源实例不存在时要抛异常，别把「复制了别的东西」当成成功。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 clone()")

    def delete(self, idx: int | str) -> None:
        """删除一台实例。

        厂商要求先停机时由 adapter 自己编排（MuMu 就是这种）——调用方不必
        知道各家规矩。删完必须能确认它真的没了，做不到就报错。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 delete()")

    def rename(self, idx: int | str, name: str) -> None:
        """改实例名。名字**不必唯一**（MuMu 允许重名），所以别拿它当标识。"""
        raise NotImplementedError(f"{type(self).__name__} 没有实现 rename()")

    def get_settings(self, idx: int | str) -> dict[str, str]:
        """读一台实例的全部设置，返回**厂商方言**的 key → 字符串值。

        key 不做中立化 —— 中立命名的价值由领域动词（`set_resolution` /
        `set_cpu` / …）提供；这个方法的用处是「一次拿到结构化的整份」，以及把
        厂商之间巨大的差异（MuMu 一条命令给 JSON；雷电没有读的 CLI，只能解析它
        自己写的配置文件）收在 adapter 后面。所以同一个 key 在两家叫不同的名字，
        跨厂商的代码别拿它当通用接口用。

        厂商连读都给不出来时抛 NotImplementedError，**不要**返回空 dict 或半份
        —— 那会让调用方以为「这台机器没有设置」。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 get_settings()")

    def set_settings(self, idx: int | str, **kv: str) -> None:
        """写设置。key 同样是厂商方言，值一律是字符串。

        至少要给一个 key：空调用不是合法请求，报 ValueError 而不是静默成功。

        **写下去的值在实例下次启动时才生效。** 厂商把「期望值」留到启动时才
        变成生效值（MuMu 可写的 `.custom` 那一批是期望值，只读的
        `resolution_width` 才是当前生效值；雷电同样把 cpuCount / memorySize
        这类字段留到下次启动）。所以本层**不替你重启**：要不要现在就看到效果是
        调用方的决定，而顺手关掉别人的模拟器是更坏的意外。要立刻生效就自己
        `reboot_device(idx)`。

        实测说明「只有停机才能写」是个误解：MuMu 的可写 key 在**运行中**写完全
        成功（对运行中的实例逐个写回 12 个关键 key，全部 errcode 0），雷电的
        `modify` 在运行中也是 rc 0 且配置立即落盘。MuMu 报的
        `key not writable` 意思是「**这个 key 只读**」，与实例在不在跑无关
        （只读的 `resolution_width` 在**停机**实例上写同样是 -101）。想知道某个
        key 可不可写，用 `setting -v <idx> -k <key> -i` 看它的 writable 字段。
        """
        if not kv:
            raise ValueError("set_settings() 至少要给一个 key=value")
        self._write_settings(idx, kv)

    def _write_settings(self, idx: int | str, kv: dict[str, str]) -> None:
        """厂商方言的写入实现，由 `set_settings` 调用。

        拆出来是为了让「至少要给一个 key」这条中立规矩只写一遍 —— 两家 adapter
        只管自己的方言。没实现的厂商默认抛 NotImplementedError。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 set_settings()")

    def set_resolution(self, idx: int | str, width: int, height: int, dpi: int) -> None:
        """把实例的分辨率设成 `width` x `height` @ `dpi`。

        这是**语义对齐**的中立动词，不是方言 key 的包装：两家都同意「宽 x 高 @
        dpi」这件事，所以它值得一个名字。厂商之间的差异收在 adapter 后面
        （MuMu 要把 `resolution_mode` 一起置成 `custom` 并写三个 `.custom`，
        雷电一次 `--resolution w,h,dpi`）。

        越界的值抛 ValueError，**不发下去**。这一条是必需的、不是保险：实测两家
        都会**静默**处理越界的分辨率 —— MuMu 把宽 100 夹到 380、99999 夹到
        4096、dpi 5000 夹到 960（**都是 rc 0、没有报错**），雷电则把越界的宽高
        **整个丢掉**（`--resolution 100,100,10` → rc 0，只有 dpi 落盘）。不自己
        拦，就等于悄悄给用户另一个分辨率。

        生效时间见 `set_settings`：值在实例下次启动时才生效，本层不替你重启。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 set_resolution()")

    def set_cpu(self, idx: int | str, cores: int) -> None:
        """把实例的 CPU 设成 `cores` 核。

        单位是**核数**（整数），这是两家都同意的那部分。可用值域是厂商定的：
        雷电只认 `1|2|3|4`；MuMu 的可选列表**随宿主机变**（实测本机 1..16，
        best=4），所以 MuMu 的 adapter 必须先把它读出来再核对。

        越界抛 ValueError、不发下去 —— 厂商自己倒是会拒（MuMu
        `-105 cpu setting not found in list`、雷电 `parameter error!`），但那句话
        只说得出「不行」，说不出**哪个值行**。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 set_cpu()")

    def set_memory(self, idx: int | str, megabytes: int) -> None:
        """把实例的内存设成 `megabytes` MB。

        单位统一是 **MB（整数）**—— 这是中立层的约定，不是任何一家的方言：
        MuMu 用 GB（`performance_mem.custom = "1.750000"`），雷电用 MB
        （`--memory 2048`）。adapter 负责换算。

        两家都只认一**档**离散的值（实测 MuMu 0.75/1/1.5/1.75/2/3…16 GB，雷电
        8 档 MB）。落在档位之间时**就近取值**（并列取大，即宁可多给不肯少给）；
        整个档位范围之外抛 ValueError，不静默夹到边界。

        生效时间见 `set_settings`。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 set_memory()")

    def set_root(self, idx: int | str, enabled: bool) -> None:
        """开/关实例的 root 权限。两家都是布尔，是这里差异最小的一个动词。

        注意它只改**配置**：已经跑着的实例不会因此当场掉权限或拿到权限，要等
        下次启动（见 `set_settings`）。
        """
        raise NotImplementedError(f"{type(self).__name__} 没有实现 set_root()")

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
