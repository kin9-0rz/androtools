"""Device 生命周期状态机的 characterization tests。

只锁真正有 implementation 的部分：状态判定、启动等待、卡死探测、厂商 PID 解析。
像 tap / install_app 这类把参数拼成 adb 命令的一行包装不锁 —— 它们是 pass-through，
锁住字符形状既无价值，又会在重构时制造噪音。

这些测试是纯单元测试：不碰模拟器，不碰 adb，也不需要 Android SDK。
"""

import subprocess
import threading
from typing import Any

import pytest

from androtools.android_sdk.platform_tools import DeviceOfflineError
from androtools.cmd.result import CmdResult
from androtools.core.device import Device, DeviceInfo, DeviceStatus, DeviceType
from androtools.core.ld import LDConsole, LDPlayer
from androtools.core.nox import NoxConsole, NoxPlayer
from androtools.testing import FakeADB

SERIAL = "127.0.0.1:5555"

# Device.get_status() 内部用的四条命令
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


class FakePlayer(Device):
    """Device 的最小 concrete 实现，用来驱动基类的生命周期逻辑。

    基类的 is_boot() 就是 `self.pid != -1`，所以控制 pid 就等于控制「是否已启动」，
    不需要碰 Console。
    """

    def __init__(self, info, adb, sleeper=lambda _s: None):
        super().__init__(info, adb=adb, sleeper=sleeper)
        self.launch_count = 0
        self.close_count = 0
        #: launch() 之后模拟器会起来；置 False 可以观察「启动后仍然起不来」的重试路径
        self.boot_after_launch = True

    def launch(self):
        self.launch_count += 1
        if self.boot_after_launch:
            self.pid = 4242

    def close(self):
        self.close_count += 1

    def install_app_by_console(self, apk_path: str) -> CmdResult:
        return CmdResult("", "")

    def uninstall_app_by_console(self, package_name: str) -> CmdResult:
        return CmdResult("", "")


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
    dev.pid = 100

    assert dev.get_status() == DeviceStatus.ERORR


def test_get_status_device_connected_but_not_booted():
    responses = booting_responses("device", boot_completed="0")
    dev, adb = make_device(responses)
    dev.pid = 100

    assert dev.get_status() == DeviceStatus.DEVICE


def test_get_status_boot_completed():
    dev, adb = make_device(booting_responses("device", boot_completed="1"))
    dev.pid = 100

    assert dev.get_status() == DeviceStatus.BOOT_COMPLETED


def test_get_status_offline():
    """offline 来自 stderr，不是 stdout。"""
    dev, adb = make_device(booting_responses("", "offline"))
    dev.pid = 100

    assert dev.get_status() == DeviceStatus.OFFLINE


def test_get_status_error_when_input_keyevent_raises():
    """get-state 说 device，但按键没反应 -> 判定为异常状态。"""
    dev, adb = make_device(
        booting_responses("device"),
        errors={ALT_LEFT: DeviceOfflineError("设备已断开")},
    )
    dev.pid = 100

    assert dev.get_status() == DeviceStatus.ERORR


def test_get_status_calls_get_state_twice_and_discards_the_first():
    """锁住现状：get_status 连续调两次 get-state，第一次的结果被丢弃。

    这是 Device.get_status() 里的一处多余调用，不在本次修正范围内 ——
    锁住它是为了让将来的清理有一个明确的起点，而不是让它继续隐身。
    """
    dev, adb = make_device(booting_responses("device"))
    dev.pid = 100

    dev.get_status()

    assert adb.count(*GET_STATE) == 2


# --------------------------------------------------------------------------- #
# launch_and_wait_for_device()
# --------------------------------------------------------------------------- #


def test_launch_and_wait_returns_immediately_when_already_booted():
    dev, adb = make_device(booting_responses("device", boot_completed="1"))
    dev.pid = 100

    assert dev.launch_and_wait_for_device() is True
    assert dev.launch_count == 0
    assert dev.status == DeviceStatus.BOOT_COMPLETED


def test_launch_and_wait_launches_when_not_booted():
    dev, adb = make_device(booting_responses("device", boot_completed="1"))

    assert dev.launch_and_wait_for_device() is True
    assert dev.launch_count == 1


def test_launch_and_wait_gives_up_after_the_retry_budget():
    """重试预算耗尽 -> 关掉设备，状态回到 STOP，返回 False。"""
    dev, adb = make_device(booting_responses("device", boot_completed="0"))
    dev.pid = 100

    assert dev.launch_and_wait_for_device() is False
    assert dev.close_count == 1
    assert dev.status == DeviceStatus.STOP


# --------------------------------------------------------------------------- #
# is_timeout() / is_crashed()
# --------------------------------------------------------------------------- #


def test_is_timeout_true_when_adb_call_times_out():
    dev, adb = make_device(
        errors={("shell", "ps"): subprocess.TimeoutExpired("adb", 3)}
    )
    dev.pid = 100

    assert dev.is_timeout(seconds=3) is True
    assert adb.commands("run_cmd") == [("shell", "ps")]


def test_is_timeout_false_when_adb_answers():
    dev, adb = make_device({("shell", "ps"): CmdResult("USER PID", "")})
    dev.pid = 100

    assert dev.is_timeout(seconds=3) is False


def test_is_crashed_true_when_home_key_never_responds():
    """is_crashed 用 5 秒的 func_timeout 包裹 home()，所以这个测试要真的等 5 秒。"""

    class HangingPlayer(FakePlayer):
        def home(self):
            threading.Event().wait(30)

    adb = FakeADB()
    dev = HangingPlayer(make_info(), adb)

    assert dev.is_crashed() is True


def test_is_crashed_false_when_home_key_works():
    dev, adb = make_device({HOME: CmdResult("", "")})
    dev.pid = 100

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


def ld_row(index, pid, vbox_pid, width=540, height=960, dpi=240):
    """ldconsole list2 的真实列序（10 列），对照 D:\\ProgramFiles\\LDPlayer9.0.79.2 实测：

        0,雷电模拟器,0,0,0,-1,-1,540,960,240
         0    1    2 3 4  5  6  7  8  9

    5 = 进程 PID，6 = VBox 进程 PID，7/8 = 分辨率宽高，9 = dpi。
    """
    return f"{index},雷电模拟器,0,0,1,{pid},{vbox_pid},{width},{height},{dpi}"


def test_ld_is_boot_requires_both_pids():
    """雷电必须 PID 和 VBox PID 同时存在才算完全启动。"""
    console = FakeLDConsole([ld_row(0, 1111, 2222)])
    dev = LDPlayer(make_info(index="0"), adb=FakeADB(), console=console)

    assert dev.is_boot() is True
    assert dev.get_pid() == 1111
    assert dev.get_vbox_pid() == 2222


def test_ld_pids_come_from_the_right_columns():
    """回归测试：PID 曾经被从第 6、7 列读，第 7 列其实是屏幕宽度。

    旧代码因此把 540（宽度）当成 VBox PID，「两个 PID 都存在」这个判断
    实际上只检查了 VBox 进程，界面进程死掉时也会判定为已启动。
    """
    console = FakeLDConsole([ld_row(0, 1111, 2222, width=540)])
    dev = LDPlayer(make_info(index="0"), adb=FakeADB(), console=console)

    dev.is_boot()

    assert dev.get_pid() == 1111  # 第 5 列
    assert dev.get_vbox_pid() == 2222  # 第 6 列，不是 540


def test_ld_is_boot_false_when_vbox_pid_missing():
    console = FakeLDConsole([ld_row(0, 1111, -1)])
    dev = LDPlayer(make_info(index="0"), adb=FakeADB(), console=console)

    assert dev.is_boot() is False
    assert dev.get_vbox_pid() == -1


def test_ld_is_boot_false_when_ui_pid_missing():
    """界面进程死了但 VM 还活着 —— 这正是「半启动」，必须判为未启动。"""
    console = FakeLDConsole([ld_row(0, -1, 2222)])
    dev = LDPlayer(make_info(index="0"), adb=FakeADB(), console=console)

    assert dev.is_boot() is False
    assert dev.get_pid() == -1


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


def test_nox_is_boot_reads_both_pids_in_the_right_columns():
    """回归测试：pid / vm_pid 曾经被装反，且 vm_pid 是 str。

    Console 最后一列是 NoxVMHandle.exe（与 adb 通信的 VM 进程），倒数第二列是
    Nox.exe（界面进程）。旧代码把最后一列写进 self.pid、把 Nox.exe 写进
    self.vm_pid，且存成 str —— 而 get_serial() 拿 vm_pid 和 psutil 返回的 int
    pid 比较，str 永远比不相等，那个 while 循环出不来。
    """
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])
    dev = NoxPlayer(make_info(index="1"), adb=FakeADB(), console=console)

    dev.is_boot()

    assert dev.get_pid() == 3333  # Nox.exe，界面进程
    assert dev.vm_pid == 4444  # NoxVMHandle.exe，VM 进程


def test_nox_vm_pid_is_an_int():
    """vm_pid 必须是 int，否则 get_serial() 里 psutil 的 pid 比较恒为 False。"""
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])
    dev = NoxPlayer(make_info(index="1"), adb=FakeADB(), console=console)

    dev.is_boot()

    assert isinstance(dev.vm_pid, int)


def test_nox_is_boot_skips_stopped_instances():
    """PID 为 -1 的实例要跳过，不能把别的实例的状态认成自己的。"""
    console = FakeNoxConsole([nox_row(1, -1, -1), nox_row(2, 5555, 6666)])
    dev = NoxPlayer(make_info(index="2"), adb=FakeADB(), console=console)

    assert dev.is_boot() is True
    assert dev.get_pid() == 5555
    assert dev.vm_pid == 6666


def test_nox_is_boot_false_when_all_stopped():
    console = FakeNoxConsole([nox_row(1, -1, -1)])
    dev = NoxPlayer(make_info(index="1"), adb=FakeADB(), console=console)

    assert dev.is_boot() is False