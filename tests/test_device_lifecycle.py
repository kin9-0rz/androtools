"""模拟器生命周期状态机的 characterization tests。

只锁真正有 implementation 的部分：状态判定、启动等待、卡死探测、厂商 PID 解析。
像 tap / install_app 这类把参数拼成 adb 命令的一行包装不锁 —— 它们是 pass-through，
锁住字符形状既无价值，又会在重构时制造噪音。

这些测试是纯单元测试：不碰模拟器，不碰 adb，也不需要 Android SDK。
"""

import inspect
import subprocess
import threading
from typing import Any

import pytest

from androtools.android_sdk.platform_tools import DeviceOfflineError
from androtools.cmd.result import CmdResult
from androtools.core.device import (
    DeviceConsole,
    DeviceInfo,
    DeviceStatus,
    DeviceType,
    Pids,
)
from androtools.core.ld import LDConsole, LDPlayer
from androtools.core.mumu import MumuPlayer
from androtools.core.nox import NoxConsole, NoxPlayer
from androtools.core.session import EmulatorSession
from androtools.core.shell import AndroidShell
from androtools.testing import FakeADB

SERIAL = "127.0.0.1:5555"

# EmulatorSession.get_status() 内部用的四条命令
RECONNECT = ("reconnect",)
GET_STATE = ("get-state",)
BOOT_COMPLETED = ("getprop", "sys.boot_completed")
ALT_LEFT = ("input", "keyevent", "57")  # KeyEvent.KEYCODE_ALT_LEFT
HOME = ("input", "keyevent", "3")  # KeyEvent.KEYCODE_HOME


def make_info(**overrides) -> DeviceInfo:
    fields: dict[str, Any] = dict(
        device_type=DeviceType.LD,
        index="0",
        serial=SERIAL,
        name="test-device",
        version=9,
        adb_path="adb",
        console_path="ldconsole",
        gateway="127.0.0.1",
        proxy_port=8080,
    )
    fields.update(overrides)
    return DeviceInfo(**fields)


class FakePlayer(EmulatorSession):
    """EmulatorSession 的最小 concrete 实现，用来驱动生命周期逻辑。

    基类的 is_boot() 就是 `self.pid != -1`，所以控制 pid 就等于控制「是否已启动」，
    不需要碰 Console —— session 的 interface 只有 launch / close 是抽象的。
    """

    def __init__(self, info, adb, sleeper=lambda _s: None):
        super().__init__(info, adb=adb, sleeper=sleeper)
        self.launch_count = 0
        self.close_count = 0
        #: is_boot() 的答案。测试直接翻它来控制「模拟器起来没有」。
        self.booted = False

    def launch(self):
        self.launch_count += 1
        self.booted = True

    def close(self):
        self.close_count += 1

    def is_boot(self) -> bool:
        return self.booted


def make_device(
    responses=None, errors=None, info=None, **kwargs
) -> tuple[FakePlayer, FakeADB]:
    adb = FakeADB(responses=responses, errors=errors)
    return FakePlayer(info or make_info(), adb, **kwargs), adb


def booting_responses(state_output="device", state_error="", boot_completed="0"):
    return {
        RECONNECT: CmdResult("", ""),
        GET_STATE: CmdResult(state_output, state_error),
        BOOT_COMPLETED: CmdResult(boot_completed, ""),
        ALT_LEFT: CmdResult("", ""),
    }


# --------------------------------------------------------------------------- #
# get_status() 的六个分支
# --------------------------------------------------------------------------- #


def test_get_status_stop_without_any_adb_call():
    """没启动时直接返回 STOP，一次 adb 都不该调。"""
    dev, adb = make_device()

    assert dev.get_status() == DeviceStatus.STOP
    assert adb.calls == []


def test_get_status_boot_when_adb_is_unreachable():
    """有 PID 但 adb 完全不认这个 serial -> ERORR。"""
    responses = booting_responses()
    responses[GET_STATE] = CmdResult("", "device 'x' not found")
    dev, adb = make_device(responses)
    dev.booted = True

    assert dev.get_status() == DeviceStatus.ERORR


def test_get_status_device_connected_but_not_booted():
    responses = booting_responses("device", boot_completed="0")
    dev, adb = make_device(responses)
    dev.booted = True

    assert dev.get_status() == DeviceStatus.DEVICE


def test_get_status_boot_completed():
    dev, adb = make_device(booting_responses("device", boot_completed="1"))
    dev.booted = True

    assert dev.get_status() == DeviceStatus.BOOT_COMPLETED


def test_get_status_offline():
    """offline 来自 stderr，不是 stdout。"""
    dev, adb = make_device(booting_responses("", "offline"))
    dev.booted = True

    assert dev.get_status() == DeviceStatus.OFFLINE


def test_get_status_error_when_input_keyevent_raises():
    """get-state 说 device，但按键没反应 -> 判定为异常状态。"""
    dev, adb = make_device(
        booting_responses("device"),
        errors={ALT_LEFT: DeviceOfflineError("设备已断开")},
    )
    dev.booted = True

    assert dev.get_status() == DeviceStatus.ERORR


def test_get_status_queries_get_state_once_when_answered():
    """回归测试：get_status 曾经无条件连调两次 get-state，且第一次的结果被丢弃。

    启动流程里 get_status 最多被调 11 次，每次多一次 adb 往返。
    """
    dev, adb = make_device(booting_responses("device"))
    dev.booted = True

    dev.get_status()

    assert adb.count(*GET_STATE) == 1


def test_get_status_retries_get_state_after_not_found():
    """"not found" 时 reconnect 之后重试一次 —— 这一段是有意保留的。"""
    responses = booting_responses()
    responses[GET_STATE] = CmdResult("", "device 'emulator-5556' not found")
    dev, adb = make_device(responses)
    dev.booted = True

    assert dev.get_status() == DeviceStatus.ERORR
    assert adb.count(*GET_STATE) == 2
    assert adb.count(*RECONNECT) == 2


# --------------------------------------------------------------------------- #
# ensure_ready()
# --------------------------------------------------------------------------- #


def test_ensure_ready_returns_immediately_when_already_booted():
    dev, adb = make_device(booting_responses("device", boot_completed="1"))
    dev.booted = True

    assert dev.ensure_ready() is True
    assert dev.launch_count == 0
    assert dev.status == DeviceStatus.BOOT_COMPLETED


def test_ensure_ready_launches_when_not_booted():
    dev, adb = make_device(booting_responses("device", boot_completed="1"))

    assert dev.ensure_ready() is True
    assert dev.launch_count == 1


def test_ensure_ready_gives_up_after_the_retry_budget():
    """重试预算耗尽 -> 关掉设备，状态回到 STOP，返回 False。"""
    dev, adb = make_device(booting_responses("device", boot_completed="0"))
    dev.booted = True

    assert dev.ensure_ready() is False
    assert dev.close_count == 1
    assert dev.status == DeviceStatus.STOP
    # 首次探测 + 11 次重试。修复 get_state 重复调用之前，这里是 24 次 adb 往返。
    assert adb.count(*GET_STATE) == 12


# --------------------------------------------------------------------------- #
# is_timeout() / is_crashed()
# --------------------------------------------------------------------------- #


def test_is_timeout_true_when_adb_call_times_out():
    dev, adb = make_device(
        errors={("shell", "ps"): subprocess.TimeoutExpired("adb", 3)}
    )
    dev.booted = True

    assert dev.is_timeout(seconds=3) is True
    assert adb.commands("run_cmd") == [("shell", "ps")]


def test_is_timeout_false_when_adb_answers():
    dev, adb = make_device({("shell", "ps"): CmdResult("USER PID", "")})
    dev.booted = True

    assert dev.is_timeout(seconds=3) is False


class HangingShell(AndroidShell):
    """home 键永远不返回 —— 模拟器已经卡死。"""

    def home(self):
        threading.Event().wait(30)


def test_is_crashed_true_when_home_key_never_responds():
    """is_crashed 用 5 秒的 func_timeout 包裹 shell.home()，所以这个测试要真的等 5 秒。"""
    adb = FakeADB()
    dev = FakePlayer(make_info(), adb)
    dev.shell = HangingShell(adb, dev.info)

    assert dev.is_crashed() is True


def test_is_crashed_false_when_home_key_works():
    dev, adb = make_device({HOME: CmdResult("", "")})
    dev.booted = True

    assert dev.is_crashed() is False


# --------------------------------------------------------------------------- #
# FakeADB 自身的行为
# --------------------------------------------------------------------------- #


def test_fake_adb_rejects_undeclared_command():
    dev, adb = make_device()

    with pytest.raises(AssertionError, match="未声明命令"):
        adb.run_cmd(["get-state"])


# --------------------------------------------------------------------------- #
# 厂商的 is_boot()：PID 从 Console 读，不从 adb 读
# --------------------------------------------------------------------------- #


class FakeLDConsole(LDConsole):
    """只替换 list_devices —— 那是 LDConsole 里唯一碰进程的地方。

    get_pids 的列解析用真实实现，否则测的就不是我们改的那段代码了。
    故意不调 super().__init__：那会去 shutil.which 找 ldconsole.exe。
    """

    def __init__(self, rows):
        self.rows = rows

    def list_devices(self) -> str:
        return "\n".join(self.rows)


def ld_row(index, pid, vm_pid, width=540, height=960, dpi=240):
    """ldconsole list2 的真实列序（10 列），对照 D:\\ProgramFiles\\LDPlayer9.0.79.2 实测：

        0,雷电模拟器,0,0,0,-1,-1,540,960,240
         0    1    2 3 4  5  6  7  8  9

    5 = 进程 PID，6 = VBox 进程 PID，7/8 = 分辨率宽高，9 = dpi。
    """
    return f"{index},雷电模拟器,0,0,1,{pid},{vm_pid},{width},{height},{dpi}"


def test_ld_probe_state_requires_both_pids():
    """雷电不给状态字符串，只能靠两个进程同时存在来判定。"""
    console = FakeLDConsole([ld_row(0, 1111, 2222)])

    assert console.probe_state("0") == DeviceStatus.BOOT
    assert console.get_pids("0") == Pids(1111, 2222)


def test_ld_pids_come_from_the_right_columns():
    """回归测试：PID 曾经被从第 6、7 列读，第 7 列其实是屏幕宽度。

    旧代码因此把 540（宽度）当成 VBox PID，「两个 PID 都存在」这个判断
    实际上只检查了 VBox 进程，界面进程死掉时也会判定为已启动。
    """
    console = FakeLDConsole([ld_row(0, 1111, 2222, width=540)])

    assert console.get_pids("0") == Pids(1111, 2222)


def test_ld_probe_state_stop_when_vm_pid_missing():
    console = FakeLDConsole([ld_row(0, 1111, -1)])

    assert console.probe_state("0") == DeviceStatus.STOP


def test_ld_probe_state_stop_when_ui_pid_missing():
    """界面进程死了但 VM 还活着 —— 这正是「半启动」，必须判为未启动。"""
    console = FakeLDConsole([ld_row(0, -1, 2222)])

    assert console.probe_state("0") == DeviceStatus.STOP


class FakeNoxConsole(NoxConsole):
    """list 输出：索引,名称,标题,工具栏句柄,Nox.exe PID,NoxVMHandle.exe PID

    同样故意不调 super().__init__，理由同 FakeLDConsole。
    """

    def __init__(self, lines):
        self.lines = lines

    def list_devices(self) -> str:
        return "\n".join(self.lines)


def nox_row(index, nox_pid, vm_pid):
    """NoxConsole.list 的列：索引,名称,标题,工具栏句柄,Nox.exe PID,NoxVMHandle PID

    注意最后一列是 NoxVMHandle.exe —— 即与 adb 通信的那个 VM 进程。
    """
    return f"{index},name,title,0,{nox_pid},{vm_pid}"


def test_both_consoles_satisfy_the_same_interface():
    """雷电和夜神是同一个 interface 上的两个 adapter，且都不再有 abstract 残留。"""
    assert issubclass(LDConsole, DeviceConsole)
    assert issubclass(NoxConsole, DeviceConsole)
    assert LDConsole.__abstractmethods__ == frozenset()
    assert NoxConsole.__abstractmethods__ == frozenset()


def test_device_console_rejects_incomplete_adapter():
    """回归测试：DeviceConsole 曾经没继承 ABC，@abstractmethod 完全不生效。"""

    class HalfConsole(DeviceConsole):
        def launch_device(self, idx):
            pass

    with pytest.raises(TypeError):
        HalfConsole("ldconsole")  # type: ignore[abstract]


def test_every_player_can_be_built_from_info_alone():
    """回归测试：console 一度是必需位置参数，LDPlayer(info) 直接 TypeError。

    各厂商 Player 各自自建 Console，所以 console 必须可选。之前的测试全都注入了
    fake console，从来没走过默认构造这条路径，所以这个回归漏了过去。
    """
    for cls in (LDPlayer, NoxPlayer, MumuPlayer):
        params = inspect.signature(cls.__init__).parameters.values()
        required = [p.name for p in params if p.default is inspect.Parameter.empty]
        assert required == ["self", "info"], f"{cls.__name__} 的必需参数是 {required}"


def test_nox_get_pids_returns_minus_one_for_unknown_index():
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])

    assert console.get_pids("9") == Pids(-1, -1)


def test_nox_get_pids_returns_minus_one_when_stopped():
    console = FakeNoxConsole([nox_row(1, -1, -1)])

    assert console.get_pids("1") == Pids(-1, -1)


def test_nox_get_pids_reads_both_pids_in_the_right_columns():
    """回归测试：pid / vm_pid 曾经被装反，且 vm_pid 是 str。

    Console 最后一列是 NoxVMHandle.exe（与 adb 通信的 VM 进程），倒数第二列是
    Nox.exe（界面进程）。旧代码把最后一列写进 self.pid、把 Nox.exe 写进
    self.vm_pid，且存成 str —— 而 get_serial() 拿 vm_pid 和 psutil 返回的 int
    pid 比较，str 永远比不相等，那个 while 循环出不来。
    """
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])

    assert console.get_pids("1") == Pids(3333, 4444)


def test_nox_get_pids_returns_ints():
    """必须是 int，否则 get_serial() 里 psutil 的 pid 比较恒为 False。"""
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])

    pids = console.get_pids("1")

    assert isinstance(pids.ui, int) and isinstance(pids.vm, int)


def test_nox_probe_state_skips_stopped_instances():
    """PID 为 -1 的实例要跳过，不能把别的实例的状态认成自己的。"""
    console = FakeNoxConsole([nox_row(1, -1, -1), nox_row(2, 5555, 6666)])

    assert console.probe_state("2") == DeviceStatus.BOOT
    assert console.get_pids("2") == Pids(5555, 6666)


def test_nox_probe_state_stop_when_all_stopped():
    console = FakeNoxConsole([nox_row(1, -1, -1)])

    assert console.probe_state("1") == DeviceStatus.STOP