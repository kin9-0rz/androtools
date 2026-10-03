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
    """只实现 LDPlayer 依赖的那几个方法；list2 的解析留在 LDConsole 自己身上。

    故意不调 super().__init__：那会去 shutil.which 找 ldconsole.exe。
    继承它只是为了保持类型标注诚实 —— Control Console 本身还不是一个真正的
    interface，那是候选 3 的工作。
    """

    def __init__(self, rows):
        self.rows = rows

    def get_pids(self, idx: int):
        parts = self.rows[idx].split(",")
        return int(parts[6]), int(parts[7])


def ld_row(index, pid, vbox_pid):
    # 0 索引, 1 标题, 2 顶层窗口句柄, 3 绑定窗口句柄, 4 运行状态, 5 进程ID, 6 VBox PID
    return f"{index},title,0,0,1,-1,{pid},{vbox_pid}"


def test_ld_is_boot_requires_both_pids():
    """雷电必须 PID 和 VBox PID 同时存在才算完全启动。"""
    console = FakeLDConsole([ld_row(0, 1111, 2222)])
    dev = LDPlayer(make_info(index="0"), adb=FakeADB(), console=console)

    assert dev.is_boot() is True
    assert dev.get_pid() == 1111
    assert dev.get_vbox_pid() == 2222


def test_ld_is_boot_false_when_vbox_pid_missing():
    console = FakeLDConsole([ld_row(0, 1111, -1)])
    dev = LDPlayer(make_info(index="0"), adb=FakeADB(), console=console)

    assert dev.is_boot() is False
    assert dev.get_pid() == 1111
    assert dev.get_vbox_pid() == -1


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


def test_nox_is_boot_true():
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])
    dev = NoxPlayer(make_info(index="1"), adb=FakeADB(), console=console)

    assert dev.is_boot() is True


def test_nox_is_boot_swaps_pid_and_vm_pid():
    """锁住现状：NoxPlayer.is_boot() 把 Nox.exe 和 NoxVMHandle.exe 的 PID 装反了。

    最后一列是 NoxVMHandle.exe（与 adb 通信的 VM 进程），但代码把它写进 self.pid，
    而把前面的 Nox.exe（界面进程）写进 self.vm_pid。后果是 get_serial() 拿
    self.vm_pid 去查监听端口，查的是界面进程的端口。

    本次不修 —— 只让它显形。修复时这两个断言就是回归测试。
    """
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])
    dev = NoxPlayer(make_info(index="1"), adb=FakeADB(), console=console)

    dev.is_boot()

    assert dev.pid == 4444  # 实际是 NoxVMHandle.exe 的 PID
    assert dev.vm_pid == "3333"  # 实际是 Nox.exe 的 PID，且是 str


def test_nox_get_pid_is_broken():
    """锁住现状：NoxPlayer.get_pid() 被覆写成恒返回 -1，即使设备已启动。

    基类 Device.get_pid() 返回 self.pid。夜神这个覆写让任何依赖 get_pid()
    的调用方拿到错误答案 —— 本次不修，只让它显形。
    """
    console = FakeNoxConsole([nox_row(1, 3333, 4444)])
    dev = NoxPlayer(make_info(index="1"), adb=FakeADB(), console=console)
    dev.is_boot()

    assert dev.get_pid() == -1


def test_nox_is_boot_skips_stopped_instances():
    """PID 为 -1 的实例要跳过，不能把别的实例的状态认成自己的。"""
    console = FakeNoxConsole([nox_row(1, -1, -1), nox_row(2, 5555, 6666)])
    dev = NoxPlayer(make_info(index="2"), adb=FakeADB(), console=console)

    assert dev.is_boot() is True
    assert dev.pid == 6666


def test_nox_is_boot_false_when_all_stopped():
    console = FakeNoxConsole([nox_row(1, -1, -1)])
    dev = NoxPlayer(make_info(index="1"), adb=FakeADB(), console=console)

    assert dev.is_boot() is False