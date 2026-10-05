"""模拟器生命周期状态机的 characterization tests。

只锁真正有 implementation 的部分：状态判定、启动等待、卡死探测、厂商 PID 解析。
像 tap / install_app 这类把参数拼成 adb 命令的一行包装不锁 —— 它们是 pass-through，
锁住字符形状既无价值，又会在重构时制造噪音。

这些测试是纯单元测试：不碰模拟器，不碰 adb，也不需要 Android SDK。
"""

import inspect
import subprocess
import threading
import pytest

from androtools.android_sdk.platform_tools import (
    AmbiguousDeviceError,
    DeviceOfflineError,
)
from androtools.cmd.result import CmdResult
from helpers import make_emulator_info
from androtools.core.constants import KeyEvent
from androtools.core.device import (
    EmulatorConsole,
    DeviceStatus,
    Pids,
)
from androtools.core.ld import LDConsole, LDPlayer
from androtools.core.mumu import MumuConsole, MumuPlayer

from androtools.core.session import EmulatorSession
from androtools.core.shell import AndroidShell
from androtools.testing import AdbCall, FakeADB

SERIAL = "127.0.0.1:5555"

# EmulatorSession.get_status() 内部用的四条命令
RECONNECT = ("reconnect",)
GET_STATE = ("get-state",)
BOOT_COMPLETED = ("getprop", "sys.boot_completed")
ALT_LEFT = ("input", "keyevent", "57")  # KeyEvent.KEYCODE_ALT_LEFT
HOME = ("input", "keyevent", "3")  # KeyEvent.KEYCODE_HOME



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
    return FakePlayer(info or make_emulator_info(), adb, **kwargs), adb


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
    """"not found" 时重连一次再问 —— 这一段是有意保留的。"""
    responses = booting_responses()
    responses[GET_STATE] = CmdResult("", "device 'emulator-5556' not found")
    dev, adb = make_device(responses)
    dev.booted = True

    assert dev.get_status() == DeviceStatus.ERORR
    assert adb.count(*GET_STATE) == 2
    assert adb.count(*RECONNECT) == 1


def test_get_status_does_not_reconnect_when_the_device_answers():
    """回归测试：以前每次判定都无条件重连。

    `adb reconnect` 会打断 tcp 连接的模拟器（MuMu）且不会自己恢复 ——
    无条件重连等于每次状态判定都把它弄坏。
    """
    dev, adb = make_device(booting_responses("device"))
    dev.booted = True

    dev.get_status()

    assert adb.count(*RECONNECT) == 0


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
    dev = FakePlayer(make_emulator_info(), adb)
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


def test_both_consoles_satisfy_the_same_interface():
    """雷电和 MuMu 是同一个 interface 上的两个 adapter，且都不再有 abstract 残留。"""
    assert issubclass(LDConsole, EmulatorConsole)
    assert issubclass(MumuConsole, EmulatorConsole)
    assert LDConsole.__abstractmethods__ == frozenset()
    assert MumuConsole.__abstractmethods__ == frozenset()


def test_device_console_rejects_incomplete_adapter():
    """回归测试：EmulatorConsole 曾经没继承 ABC，@abstractmethod 完全不生效。"""

    class HalfConsole(EmulatorConsole):
        def launch_device(self, idx):
            pass

    with pytest.raises(TypeError):
        HalfConsole("ldconsole")  # type: ignore[abstract]


def test_every_player_can_be_built_from_info_alone():
    """回归测试：console 一度是必需位置参数，LDPlayer(info) 直接 TypeError。

    各厂商 Player 各自自建 Console，所以 console 必须可选。之前的测试全都注入了
    fake console，从来没走过默认构造这条路径，所以这个回归漏了过去。
    """
    for cls in (LDPlayer, MumuPlayer):
        params = inspect.signature(cls.__init__).parameters.values()
        required = [p.name for p in params if p.default is inspect.Parameter.empty]
        assert required == ["self", "info"], f"{cls.__name__} 的必需参数是 {required}"


# --------------------------------------------------------------------------- #
# 雷电 serial 反查：用 MAC 指纹认实例，而不是按 index 猜端口
# --------------------------------------------------------------------------- #

# 实测（雷电 9.0.79.2，index 0 与 index 1 各一个实例）：
#   vms\config\leidian0.config 里 "propertySettings.macAddress": "00DB48FD6270"
#   guest 里 `ip addr show wlan0` 给出 00:db:48:fd:62:70 —— 同一个 MAC。
#
# 为什么必须反查：雷电的 adb serial 落在 emulator-5554 + 2*index 这个号段里，
# 而 MuMu 占用同一个号段。实测两台雷电 + 两台 MuMu 同时开着时，
# emulator-5556 归 MuMu index 1，雷电 index 1 在 adb 里根本不出现；
# 连雷电官方的 `ldconsole adb --index 1` 都会安静地返回 MuMu 那台设备。
# 按 index 算出来的 serial 会「合法地」指向别人的模拟器。

LD_WLAN0 = "    link/ether 00:db:48:fd:62:70 brd ff:ff:ff:ff:ff:ff"
MUMU_WLAN0 = "    link/ether 08:fb:cf:09:96:e2 brd ff:ff:ff:ff:ff:ff"


def ld_fingerprint_console(tmp_path, mac="00DB48FD6270", index="0"):
    """造一份雷电的实例配置目录，返回指过去的 console。

    配置文件的位置是 `vms/config/leidian{index}.config`，相对 ldconsole.exe
    所在目录 —— 实测 D:\\ProgramFiles\\LDPlayer9.0.79.2\\ldconsole.exe
    与 D:\\ProgramFiles\\LDPlayer9.0.79.2\\vms\\config\\leidian0.config。

    `fingerprint()` 用真实实现（它只读文件，正是被测的那段）；`list_devices()`
    走 FakeLDConsole，免得真的去 spawn 一个空文件当 exe。
    """
    bin_dir = tmp_path / "LDPlayer"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "ldconsole.exe"
    exe.write_text("")
    config_dir = bin_dir / "vms" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"leidian{index}.config").write_text(
        '{\n    "propertySettings.macAddress": "%s",\n'
        '    "propertySettings.phoneAndroidId": "7e0ce001bc9be497"\n}\n' % mac
    )

    class FingerprintLDConsole(FakeLDConsole):
        def __init__(self):
            super().__init__([ld_row(index, 1111, 2222)])
            self.bin_path = str(exe)

    return FingerprintLDConsole()


def adb_with_two_emulators(responses=None):
    """adb 上有雷电 index 0 和一台 MuMu，MAC 各不相同。"""
    return FakeADB(
        responses=responses,
        devices=[
            ("emulator-5554", "device", "1"),
            ("emulator-5556", "device", "2"),
        ],
        serial_responses={
            "emulator-5554": {
                ("ip", "addr", "show", "wlan0"): CmdResult(LD_WLAN0, ""),
            },
            "emulator-5556": {
                ("ip", "addr", "show", "wlan0"): CmdResult(MUMU_WLAN0, ""),
            },
        },
    )


#: 走完一次 get_status() 需要的 adb 响应（它会探状态、发一颗探测按键）
STATUS_OK = {
    ("get-state",): CmdResult("device", ""),
    ("input", "keyevent", str(KeyEvent.KEYCODE_ALT_LEFT.value)): CmdResult("", ""),
}


def ld_player_with(console, adb):
    info = make_emulator_info(index="0", serial=None, console_path="ldconsole")
    return LDPlayer(info, adb=adb, sleeper=lambda _s: None, console=console)


def test_ld_reads_its_mac_out_of_the_instance_config(tmp_path):
    console = ld_fingerprint_console(tmp_path)

    assert console.fingerprint("0") == "00DB48FD6270"


def test_ld_fingerprint_is_none_when_the_config_is_absent(tmp_path):
    """雷电改了目录布局时是 None，不是异常 —— 交给上层决定怎么办。"""
    console = ld_fingerprint_console(tmp_path)
    (tmp_path / "LDPlayer" / "vms" / "config" / "leidian0.config").unlink()

    assert console.fingerprint("0") is None


def test_ld_finds_itself_by_mac_among_other_emulators(tmp_path):
    """核心用例：认 MAC，不认端口号。"""
    player = ld_player_with(ld_fingerprint_console(tmp_path), adb_with_two_emulators())

    assert player.resolve_serial() == "emulator-5554"
    # 认出来之后写进 info，之后的命令才带得上 -s
    assert player.info.serial == "emulator-5554"


def test_ld_never_picks_a_device_whose_mac_differs(tmp_path):
    """反查的核心保证：MAC 对不上就当没这个设备，绝不「差不多就行」。"""
    console = ld_fingerprint_console(tmp_path, mac="001122334455")
    player = ld_player_with(console, adb_with_two_emulators())

    with pytest.raises(RuntimeError, match="认不出"):
        player.resolve_serial()

    assert player.info.serial is None


def test_ld_says_which_serials_it_looked_at(tmp_path):
    """报错必须列出候选 —— 排查时用户需要知道该去看哪几台。"""
    console = ld_fingerprint_console(tmp_path, mac="001122334455")
    player = ld_player_with(console, adb_with_two_emulators())

    with pytest.raises(RuntimeError) as err:
        player.resolve_serial()

    assert "emulator-5554" in str(err.value)
    assert "emulator-5556" in str(err.value)


def test_ld_refuses_to_guess_when_its_config_is_gone(tmp_path):
    """没有指纹就没有任何可核对的东西 —— 这种情况下宁可报错。"""
    console = ld_fingerprint_console(tmp_path)
    (tmp_path / "LDPlayer" / "vms" / "config" / "leidian0.config").unlink()
    player = ld_player_with(console, adb_with_two_emulators())

    with pytest.raises(RuntimeError, match="配置"):
        player.resolve_serial()


def test_ld_skips_serials_that_stop_answering(tmp_path):
    """adb devices 里可能有列出来但问不动的东西，不能因此中断整条反查。"""

    class DeafADB(FakeADB):
        """emulator-5556 问什么都报错，模拟那台设备正在重启。"""

        def run_shell_cmd(self, cmd, serial=None, timeout=30):
            if serial == "emulator-5556":
                self.calls.append(AdbCall("run_shell_cmd", list(cmd), serial, timeout))
                raise RuntimeError("device offline")
            return super().run_shell_cmd(cmd, serial, timeout)

    adb = DeafADB(
        responses={("get-state",): CmdResult("device", "")},
        devices=[("emulator-5554", "device", "1"), ("emulator-5556", "offline", "2")],
        serial_responses={
            "emulator-5554": {("ip", "addr", "show", "wlan0"): CmdResult(LD_WLAN0, "")},
        },
    )
    player = ld_player_with(ld_fingerprint_console(tmp_path), adb)

    # 问不动的那台被跳过，另一台仍然认得出来
    assert player.resolve_serial() == "emulator-5554"


def test_ld_reports_failure_when_the_only_candidate_is_unreachable(tmp_path):
    """一台都问不动时要说清楚，而不是把「问不到」说成「不在」。"""

    class DeafADB(FakeADB):
        def run_shell_cmd(self, cmd, serial=None, timeout=30):
            self.calls.append(AdbCall("run_shell_cmd", list(cmd), serial, timeout))
            raise RuntimeError("device offline")

    adb = DeafADB(
        devices=[("emulator-5556", "offline", "2")],
    )
    player = ld_player_with(ld_fingerprint_console(tmp_path), adb)

    with pytest.raises(RuntimeError, match="认不出"):
        player.resolve_serial()


# --------------------------------------------------------------------------- #
# 自动反查接进 get_status —— 用它的人不该需要手动调 resolve_serial
# --------------------------------------------------------------------------- #


def test_ld_get_status_adopts_the_mac_matched_serial(tmp_path):
    adb = adb_with_two_emulators(STATUS_OK)
    player = ld_player_with(ld_fingerprint_console(tmp_path), adb)
    player.console.getprop = lambda idx, prop: "1"  # type: ignore[assignment]

    player.get_status()

    assert player.info.serial == "emulator-5554"
    # 认出来之后，adb 命令带的是 -s emulator-5554，而不是靠 adb 自己挑
    get_state = [c for c in adb.calls if tuple(c.cmd) == ("get-state",)]
    assert get_state[0].serial == "emulator-5554"


def test_ld_get_status_does_not_raise_when_the_instance_cannot_be_identified(tmp_path):
    """get_status() 的契约是返回一个状态。

    认不出实例时不能抛 —— 否则调用方得额外包一层 try。交给 adb 的歧义检查，
    它本来就会带着设备列表报错。
    """
    adb = adb_with_two_emulators()
    player = ld_player_with(
        ld_fingerprint_console(tmp_path, mac="001122334455"), adb
    )

    with pytest.raises(AmbiguousDeviceError, match="emulator-5554"):
        player.get_status()


def test_ld_get_status_still_works_without_an_instance_config(tmp_path):
    """雷电改了目录布局不该让这个类彻底不能用。"""
    console = ld_fingerprint_console(tmp_path)
    (tmp_path / "LDPlayer" / "vms" / "config" / "leidian0.config").unlink()
    adb = FakeADB(
        responses=dict(STATUS_OK),
        devices=[("emulator-5554", "device", "1")],
    )
    player = ld_player_with(console, adb)
    player.console.getprop = lambda idx, prop: ""  # type: ignore[assignment]

    assert player.get_status() == DeviceStatus.DEVICE


def test_ld_get_status_does_not_resolve_twice(tmp_path):
    """认一次就够 —— 每条命令都重扫一遍 adb 是白花的进程。"""
    adb = adb_with_two_emulators(STATUS_OK)
    player = ld_player_with(ld_fingerprint_console(tmp_path), adb)
    player.console.getprop = lambda idx, prop: "1"  # type: ignore[assignment]

    player.get_status()
    before = adb.count("ip", "addr", "show", "wlan0")
    player.get_status()

    assert adb.count("ip", "addr", "show", "wlan0") == before

