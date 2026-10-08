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
    EmulatorInstance,
    Pids,
)
from androtools.core.ld import LDConsole, LDPlayer
from androtools.core.mumu import MumuConsole, MumuPlayer
from androtools.core.identity import DiscoveredDevice, InstanceIdentity, discover, identify

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
    bin_path 直接给个名字，list_instances() 才拼得出 EmulatorInfo。
    """

    def __init__(self, rows):
        self.rows = rows
        self.bin_path = "ldconsole.exe"
        self.reads = 0

    def list_devices(self) -> str:
        self.reads += 1
        return "\n".join(self.rows)


def ld_row(index, pid, vm_pid, width=540, height=960, dpi=240, name="雷电模拟器", state=1):
    """ldconsole list2 的真实列序（10 列），对照 D:\\ProgramFiles\\LDPlayer9.0.79.2 实测：

        0,雷电模拟器,0,0,0,-1,-1,540,960,240
         0    1    2 3 4  5  6  7  8  9

    5 = 进程 PID，6 = VBox 进程 PID，7/8 = 分辨率宽高，9 = dpi。

    第 4 列（运行状态）和名字默认是实测值。测试故意让它们可覆盖：多个已有的
    用例依赖「第 4 列说运行中，但两个 PID 说没跑」这组数据。
    """
    return f"{index},{name},0,0,{state},{pid},{vm_pid},{width},{height},{dpi}"


class ScriptedLDConsole(LDConsole):
    """会真的增删改 rows 的 fake，用来测「发命令 + 回读 list2 校验效果」。

    真实 ldconsole 的退出码语义不统一（`add` 成功时就是新 index，`remove` 失败
    时是 -617），错误还打在 stdout 上（`player don't exist!`）。所以 LDConsole
    不解析任何厂商文案，只回读效果 —— 这个 fake 就是为那条路径准备的：命令
    照做，或者在 `refusals` 里声明「这条命令会静默什么都不做」。
    """

    def __init__(self, rows, refusals=()):
        self.rows = list(rows)
        self.bin_path = "ldconsole.exe"
        self.calls: list[list[str]] = []
        self.refusals = set(refusals)

    def list_devices(self) -> str:
        return "\n".join(self.rows)

    def _run(self, cmd, shell=False, encoding=None, timeout=None):  # type: ignore[override]
        self.calls.append(list(cmd))
        if cmd[0] in self.refusals:
            return CmdResult("", "")
        if cmd[0] == "add":
            idx = self._free_index()
            name = cmd[cmd.index("--name") + 1] if "--name" in cmd else self._default_name(idx)
            self.rows.append(ld_row(idx, -1, -1, name=name))
        elif cmd[0] == "copy":
            source = cmd[cmd.index("--from") + 1]
            row = next(r for r in self.rows if r.split(",")[0] == source)
            idx = self._free_index()
            name = cmd[cmd.index("--name") + 1] if "--name" in cmd else f"{row.split(',')[1]}-{idx}"
            self.rows.append(ld_row(idx, -1, -1, name=name))
        elif cmd[0] == "remove":
            target = cmd[cmd.index("--index") + 1]
            self.rows = [r for r in self.rows if r.split(",")[0] != target]
        elif cmd[0] == "rename":
            self._set_name(cmd[cmd.index("--index") + 1], cmd[cmd.index("--title") + 1])
        return CmdResult("", "")

    def _default_name(self, idx: int) -> str:
        """实测：index 0 叫「雷电模拟器」，≥1 叫「雷电模拟器-<index>」。"""
        return "雷电模拟器" if idx == 0 else f"雷电模拟器-{idx}"

    def _free_index(self) -> int:
        used = {int(r.split(",")[0]) for r in self.rows}
        idx = 0
        while idx in used:
            idx += 1
        return idx

    def _set_name(self, index: str, name: str) -> None:
        out = []
        for row in self.rows:
            cells = row.split(",")
            if cells[0] == index:
                cells[1] = name
            out.append(",".join(cells))
        self.rows = out


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


def test_ld_pids_are_matched_by_index_not_by_line_number():
    """回归测试：get_pids 曾经用 lines[int(idx)]，把行号当成了 index。

    实例被删过之后 index 有空洞（只剩 0 和 2 时第 3 行根本不存在），行号匹配
    要么 IndexError，要么错位后静默取到**另一个实例**的 PID。
    """
    console = FakeLDConsole([ld_row(0, 1111, 2222), ld_row(2, 3333, 4444)])

    assert console.get_pids("2") == Pids(3333, 4444)
    assert console.get_pids("0") == Pids(1111, 2222)


def test_ld_pids_of_a_missing_index_are_not_another_instances():
    console = FakeLDConsole([ld_row(0, 1111, 2222)])

    assert console.get_pids("1") == Pids(-1, -1)
    assert console.probe_state("1") == DeviceStatus.STOP


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


# --------------------------------------------------------------------------- #
# list_instances —— 起点：有几台、哪台在跑（两个 adapter 各自答一次）
# --------------------------------------------------------------------------- #


def test_ld_list_instances_reads_every_row():
    console = FakeLDConsole([ld_row(0, 1111, 2222), ld_row(1, -1, -1)])

    instances = console.list_instances()

    assert [i.index for i in instances] == ["0", "1"]
    assert instances[0].is_running is True
    assert instances[1].is_running is False


def test_ld_list_instances_trusts_the_two_pids_not_the_state_column():
    """第 4 列说「运行中」，但两个 PID 不都在 —— 那是半启动，必须是 STOP。

    回归风险：拿第 4 列当运行状态，会把「界面进程死了但 VBox 还活着」报成在跑，
    和 probe_state() 的判据分裂成两套。
    """
    console = FakeLDConsole([ld_row(0, 1111, -1)])

    assert console.list_instances()[0].status == DeviceStatus.STOP
    assert console.list_instances()[0].status == console.probe_state("0")


def test_ld_list_instances_has_no_serial_and_no_version():
    """serial 要拿 MAC 指纹去 adb 上反查，android_version 雷电根本不报。

    列实例是纯 Console 调用，不该因此依赖 adb。
    """
    instance = FakeLDConsole([ld_row(0, 1111, 2222)]).list_instances()[0]

    assert instance.serial is None
    assert instance.android_version is None
    assert instance.info.console_path == "ldconsole.exe"


def test_ld_list_instances_reads_the_device_list_once():
    """一次 list2 回答全部实例；逐台再问一次既慢又可能拿到不一致的快照。"""
    console = FakeLDConsole([ld_row(0, 1111, 2222), ld_row(1, -1, -1)])

    console.list_instances()

    assert console.reads == 1


def test_list_instances_is_a_soft_member_of_the_interface():
    """厂商没实现就报出来，**不能**返回空列表。

    空列表会被读成「一台实例都没有」，那是错的——而且很难发现。
    """
    with pytest.raises(NotImplementedError, match="没有实现 list_instances"):
        EmulatorConsole.list_instances(MumuConsole.__new__(MumuConsole))


def test_the_lifecycle_verbs_are_soft_members_of_the_interface():
    """四个增删改动词也是软成员：厂商没实现就报出来，**不能**静默成功。

    静默成功比报错危险得多 —— 调用方会以为实例建好了、删掉了，然后去 launch
    一个根本不存在的东西。
    """
    bare = MumuConsole.__new__(MumuConsole)

    for call in (
        lambda: EmulatorConsole.create(bare),
        lambda: EmulatorConsole.clone(bare, "0"),
        lambda: EmulatorConsole.delete(bare, "0"),
        lambda: EmulatorConsole.rename(bare, "0", "新名字"),
    ):
        with pytest.raises(NotImplementedError):
            call()


# --------------------------------------------------------------------------- #
# create / clone / delete / rename —— 雷电：发命令 + 回读 list2 校验效果
# --------------------------------------------------------------------------- #


def test_ld_create_returns_the_index_that_showed_up():
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)])

    new = console.create()

    assert new == "1"
    assert [i.index for i in console.list_instances()] == ["0", "1"]
    assert console.calls[-1] == ["add"]


def test_ld_create_passes_the_name_through():
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)])

    console.create("新实例")

    assert console.calls[-1] == ["add", "--name", "新实例"]
    assert [i.name for i in console.list_instances()][-1] == "新实例"


def test_ld_create_reports_when_no_instance_showed_up():
    """命令「成功」但 list2 里没多出东西 —— 不能返回一个假的 index。

    真实场景：ldconsole 的 `add` 就是有「退出码 0 但什么都没建」这类样子
    （它成功时的退出码反而是新 index，语义根本不统一）。
    """
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)], refusals={"add"})

    with pytest.raises(RuntimeError, match="没有出现新实例"):
        console.create()


def test_ld_clone_copies_from_the_source_index():
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)])

    new = console.clone("0", "副本")

    assert new == "1"
    assert console.calls[-1] == ["copy", "--name", "副本", "--from", "0"]


def test_ld_clone_of_a_missing_source_never_reaches_the_console():
    """实测：源不存在时 `copy` 退出码 -616、而且把**整篇 help** 打到 stdout。

    先读 list2 判存在，就不会让那一大篇 help 混进错误信息里。
    """
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)])

    with pytest.raises(RuntimeError, match="没有 index 为 9 的实例"):
        console.clone("9")

    assert console.calls == []


def test_ld_delete_verifies_the_instance_is_gone():
    console = ScriptedLDConsole([ld_row(0, 1111, 2222), ld_row(1, -1, -1)])

    console.delete("1")

    assert [i.index for i in console.list_instances()] == ["0"]
    assert console.calls[-1] == ["remove", "--index", "1"]


def test_ld_delete_of_a_missing_index_never_reaches_the_console():
    """实测：坏 index 时 `remove` 退出码 -617、stdout 是 `player don't exist!`。"""
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)])

    with pytest.raises(RuntimeError, match="没有 index 为 9 的实例"):
        console.delete("9")

    assert console.calls == []


def test_ld_delete_says_so_when_the_instance_survives():
    """删不掉就报出来，而不是假装成功。

    雷电删除运行中的实例我们没实测过（唯一在跑的是用户的实例），所以不认识
    的编排不写 —— 只把事实报清楚，并给出可操作的建议。
    """
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)], refusals={"remove"})

    with pytest.raises(RuntimeError, match="请先 close"):
        console.delete("0")


def test_ld_rename_uses_title_and_verifies_the_change():
    """`rename` 的 `--title` 才是新名字；`--index` 是选择器，不是名字。

    help 原文是 `rename [--name <mnq_name | mnq_idx>] --title <mnq_title>`，
    那里的 `--name` 是选择器的别名，容易看错。
    """
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)])

    console.rename("0", "新名字")

    assert console.calls[-1] == ["rename", "--index", "0", "--title", "新名字"]
    assert console.list_instances()[0].name == "新名字"


def test_ld_rename_to_the_same_name_is_not_an_error():
    """list2 看不出变化，但「本来就叫这个名」不是失败，发命令只会把 no-op 报成错。"""
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)])

    console.rename("0", "雷电模拟器")

    assert console.calls == []


def test_ld_rename_reports_when_the_title_did_not_change():
    console = ScriptedLDConsole([ld_row(0, 1111, 2222)], refusals={"rename"})

    with pytest.raises(RuntimeError, match="名字还是"):
        console.rename("0", "新名字")


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


# --------------------------------------------------------------------------- #
# 反查：adb serial -> 是哪个厂商的哪个实例
# --------------------------------------------------------------------------- #
#
# 撞号之后用户最需要问的一句话是「adb 上这个 emulator-5556 到底是谁的」。
# 实测 MuMu 开着时 `ldconsole adb --index 1` 会返回 MuMu 那台设备的属性，
# 所以这个反查不能问厂商工具 —— 只能拿指纹自己核对。


def test_identify_reports_the_vendor_and_index_of_a_serial(tmp_path):
    adb = adb_with_two_emulators()
    console = ld_fingerprint_console(tmp_path, mac="00DB48FD6270")

    identity = identify(adb, [("雷电", console)], "emulator-5554")

    assert identity == InstanceIdentity(
        vendor="雷电", index="0", serial="emulator-5554"
    )


def test_identify_tells_two_ld_instances_apart_by_mac():
    """两台雷电同时开着：指纹是唯一能区分它们的东西。

    实测两台的 ro.product.device 都是 marlin —— 任何按属性匹配的做法都会在这里失败。
    """

    class TwoInstances(FakeLDConsole):
        def __init__(self):
            super().__init__([ld_row(0, 1, 2), ld_row(1, 3, 4)])

        def fingerprint(self, idx):
            return {"0": "00DB48FD6270", "1": "00DB0BFC4D61"}[str(idx)]

    adb = FakeADB(
        responses={},
        devices=[("emulator-5554", "device", "1"), ("emulator-5556", "device", "2")],
        serial_responses={
            "emulator-5554": {
                ("ip", "addr", "show", "wlan0"): CmdResult(LD_WLAN0, "")
            },
            "emulator-5556": {
                ("ip", "addr", "show", "wlan0"): CmdResult(
                    "link/ether 00:db:0b:fc:4d:61 brd ff:ff:ff:ff:ff:ff", ""
                )
            },
        },
    )
    consoles = [("雷电", TwoInstances())]

    # 反查的用处：serial 本身不告诉你 index，撞号时必须靠指纹
    assert identify(adb, consoles, "emulator-5556") == InstanceIdentity(
        vendor="雷电", index="1", serial="emulator-5556"
    )
    assert identify(adb, consoles, "emulator-5554") == InstanceIdentity(
        vendor="雷电", index="0", serial="emulator-5554"
    )


def test_identify_returns_none_when_no_instance_matches(tmp_path):
    adb = adb_with_two_emulators()
    console = ld_fingerprint_console(tmp_path, mac="001122334455")

    assert identify(adb, [("雷电", console)], "emulator-5554") is None


def test_identify_returns_none_for_a_phone(tmp_path):
    """真机的 serial 认不出是哪家模拟器 —— 那不是错误，是它本来就不属于这里。"""
    adb = FakeADB(
        devices=[("A87V026107005402", "device", "1")],
        serial_responses={
            "A87V026107005402": {
                ("ip", "addr", "show", "wlan0"): CmdResult(
                    "link/ether 12:34:56:78:9a:bc", ""
                )
            }
        },
    )

    identity = identify(
        adb, [("雷电", ld_fingerprint_console(tmp_path))], "A87V026107005402"
    )

    assert identity is None


def test_identify_asks_the_device_exactly_once(tmp_path):
    """认一台设备就是一条 adb 往返，别因为有多个厂商就重复问。"""
    adb = adb_with_two_emulators()

    identify(
        adb,
        [
            ("雷电", ld_fingerprint_console(tmp_path, mac="001122334455")),
            ("雷电二号", ld_fingerprint_console(tmp_path, mac="009988776655", index="1")),
        ],
        "emulator-5554",
    )

    assert adb.count("ip", "addr", "show", "wlan0") == 1


def test_identify_does_not_enumerate_adb_devices(tmp_path):
    """反查问的是一个具体 serial —— 不该去扫整张设备表。"""
    adb = adb_with_two_emulators()

    identify(adb, [("雷电", ld_fingerprint_console(tmp_path))], "emulator-5554")

    assert adb.commands().count(("devices", "-l")) == 0


def test_identify_skips_a_vendor_without_a_fingerprint(tmp_path):
    """拿不出指纹的厂商（比如 MuMu）要跳过，而不是拿 None 去比对。

    拿 None 当期望值会让任何 MAC 读不出来的设备都「匹配上」。
    """

    class BlindConsole(MumuConsole):
        def __init__(self):
            pass

        def instances(self):
            return ["0", "1"]

        def fingerprint(self, idx):
            return None

    adb = FakeADB(
        devices=[("emulator-5556", "device", "2")],
        serial_responses={
            "emulator-5556": {
                ("ip", "addr", "show", "wlan0"): CmdResult(LD_WLAN0, "")
            }
        },
    )

    assert identify(adb, [("MuMu", BlindConsole())], "emulator-5556") is None


def test_identify_returns_none_when_the_device_cannot_be_asked(tmp_path):
    """问不动的那台要返回 None，而不是把异常抛给调用方。"""

    class DeafADB(FakeADB):
        def run_shell_cmd(self, cmd, serial=None, timeout=30):
            self.calls.append(AdbCall("run_shell_cmd", list(cmd), serial, timeout))
            raise RuntimeError("device offline")

    adb = DeafADB(devices=[("emulator-5554", "offline", "1")])

    assert identify(adb, [("雷电", ld_fingerprint_console(tmp_path))], "emulator-5554") is None


def test_identify_with_no_consoles_at_all():
    adb = FakeADB(devices=[("emulator-5554", "device", "1")])

    assert identify(adb, [], "emulator-5554") is None


def test_mumu_has_no_fingerprint_to_offer():
    """MuMu 的 imei 在 guest 侧读不回来，所以拿不出指纹。

    这不是「还没写」，是实测结论 —— 锁住它，免得哪天有人顺手填个猜的值。
    """
    assert MumuConsole.__new__(MumuConsole).fingerprint("0") is None


def test_both_consoles_can_enumerate_their_instances(tmp_path):
    ld = ld_fingerprint_console(tmp_path)

    class TwoLD(FakeLDConsole):
        def __init__(self):
            super().__init__([ld_row(0, 1, 2), ld_row(1, 3, 4)])

    class TwoMuMu(MumuConsole):
        def __init__(self):
            pass

        def _call(self, *args):
            return {"0": {"index": "0"}, "1": {"index": "1"}}

    assert TwoLD().instances() == ["0", "1"]
    assert TwoMuMu().instances() == ["0", "1"]


# --------------------------------------------------------------------------- #
# discover：列出在线设备，一台物理机器算一台
# --------------------------------------------------------------------------- #
#
# 为什么要去重：MuMu 会自己注册一个 emulator-555X，而 MumuPlayer.ensure_connected()
# 还会 adb connect 它自己那个 127.0.0.1:<adb_port> —— 同一台机器于是有两个 adb 名字。
# 实测：跑一次 MumuPlayer.get_status() 之后，adb 上 4 个条目只对应 3 台机器，
# 127.0.0.1:16416 与 emulator-5556 的 wlan0 MAC 都是 0879791ACF72。
# 直接把 adb devices 翻译成列表会把 3 台说成 4 台。


def mac_of(text):
    return CmdResult(text, "")


def three_machines_one_with_two_names():
    """手机 + 雷电 + MuMu，其中 MuMu 有两个 adb 名字。

    每台都声明了 ro.product.model 和 wlan0 MAC。
    """
    model = CmdResult("MODEL", "")
    return FakeADB(
        responses={("getprop", "ro.product.model"): model},
        devices=[
            ("bdfbafac", "device", "1"),
            ("127.0.0.1:16416", "device", "2"),
            ("emulator-5554", "device", "3"),
            ("emulator-5556", "device", "4"),
        ],
        serial_responses={
            "bdfbafac": {
                ("ip", "addr", "show", "wlan0"): mac_of("link/ether 96:c7:44:4e:d3:37 brd ff:ff:ff:ff:ff:ff"),
                ("getprop", "ro.product.model"): CmdResult("PFFM10", ""),
            },
            "127.0.0.1:16416": {
                ("ip", "addr", "show", "wlan0"): mac_of("link/ether 08:79:79:1a:cf:72 brd ff:ff:ff:ff:ff:ff"),
                ("getprop", "ro.product.model"): CmdResult("SM-A5560", ""),
            },
            "emulator-5554": {
                ("ip", "addr", "show", "wlan0"): mac_of(LD_WLAN0),
                ("getprop", "ro.product.model"): CmdResult("GM1910", ""),
            },
            "emulator-5556": {
                ("ip", "addr", "show", "wlan0"): mac_of("link/ether 08:79:79:1a:cf:72 brd ff:ff:ff:ff:ff:ff"),
                ("getprop", "ro.product.model"): CmdResult("SM-A5560", ""),
            },
        },
    )


def test_discover_counts_physical_machines_not_adb_entries():
    entries = discover(three_machines_one_with_two_names())

    assert len(entries) == 3


def test_discover_keeps_both_names_of_the_same_machine():
    """别名必须留着 —— 用户可能就是拿着 127.0.0.1:16416 来的。"""
    entries = discover(three_machines_one_with_two_names())
    mumu = [e for e in entries if "emulator-5556" in e.serials][0]

    assert sorted(mumu.serials) == ["127.0.0.1:16416", "emulator-5556"]


def test_discover_records_the_primary_serial_as_the_one_adb_lists_first():
    entries = discover(three_machines_one_with_two_names())
    mumu = [e for e in entries if "emulator-5556" in e.serials][0]

    assert mumu.serial == "127.0.0.1:16416"


def test_discover_identifies_the_ld_instances(tmp_path):
    """认得出的就标出来，认不出的留空 —— 型号不能代替身份。"""
    console = ld_fingerprint_console(tmp_path, mac="00DB48FD6270")

    entries = discover(three_machines_one_with_two_names(), [("雷电", console)])

    ld = [e for e in entries if "emulator-5554" in e.serials][0]
    assert ld.identity == InstanceIdentity(
        vendor="雷电", index="0", serial="emulator-5554"
    )
    mumu = [e for e in entries if "emulator-5556" in e.serials][0]
    assert mumu.identity is None  # MuMu 拿不出指纹


def test_discover_leaves_the_phone_without_an_identity(tmp_path):
    entries = discover(
        three_machines_one_with_two_names(),
        [("雷电", ld_fingerprint_console(tmp_path, mac="00DB48FD6270"))],
    )

    phone = [e for e in entries if "bdfbafac" in e.serials][0]

    assert phone.identity is None
    assert phone.serials == ["bdfbafac"]


def test_discover_names_the_device_from_its_model():
    entries = discover(three_machines_one_with_two_names())

    names = {e.serial: e.name for e in entries}

    assert names["emulator-5554"] == "GM1910"
    assert names["bdfbafac"] == "PFFM10"


def test_discover_splits_two_machines_that_look_alike():
    """两台同型号的设备 MAC 不同，必须是两条。"""
    adb = FakeADB(
        responses={("getprop", "ro.product.model"): CmdResult("GM1910", "")},
        devices=[("emulator-5554", "device", "1"), ("emulator-5556", "device", "2")],
        serial_responses={
            "emulator-5554": {("ip", "addr", "show", "wlan0"): mac_of("link/ether 00:db:48:fd:62:70 brd ff:ff:ff:ff:ff:ff")},
            "emulator-5556": {("ip", "addr", "show", "wlan0"): mac_of("link/ether 00:db:0b:fc:4d:61 brd ff:ff:ff:ff:ff:ff")},
        },
    )

    assert len(discover(adb)) == 2


def test_discover_keeps_a_device_it_cannot_ask():
    """离线或正在重启的设备问不到 MAC，但它确实在线，不能从列表里消失。"""

    class DeafADB(FakeADB):
        def run_shell_cmd(self, cmd, serial=None, timeout=30):
            if serial == "emulator-5554":
                self.calls.append(AdbCall("run_shell_cmd", list(cmd), serial, timeout))
                raise RuntimeError("device offline")
            return super().run_shell_cmd(cmd, serial, timeout)

    adb = DeafADB(
        responses={("getprop", "ro.product.model"): CmdResult("GM1910", "")},
        devices=[("emulator-5554", "offline", "1"), ("emulator-5556", "device", "2")],
        serial_responses={
            "emulator-5556": {
                ("ip", "addr", "show", "wlan0"): mac_of("link/ether 00:db:0b:fc:4d:61 brd ff:ff:ff:ff:ff:ff")
            }
        },
    )

    entries = discover(adb)

    # 问不动的那台仍然在列表里 —— 它确实在线（adb 认到了），不能凭空消失
    assert [e.serial for e in entries] == ["emulator-5554", "emulator-5556"]
    # 完全问不到的设备连机型都读不出来，名字诚实回退到 serial
    assert entries[0].name == "emulator-5554"
    assert entries[0].identity is None


def test_discover_falls_back_to_the_serial_when_the_model_is_unknown():
    adb = FakeADB(
        devices=[("emulator-5554", "device", "1")],
        serial_responses={
            "emulator-5554": {("ip", "addr", "show", "wlan0"): mac_of(LD_WLAN0)}
        },
    )

    assert discover(adb)[0].name == "emulator-5554"


# --------------------------------------------------------------------------- #
# get_settings / set_settings —— 读配置、写 modify
# --------------------------------------------------------------------------- #

# 实测：雷电实例的 leidian{n}.config，只留我们关心的字段。
# 注意顶层 key **本身就是点号名字**，而 resolution 的值是嵌套 dict。
LD_CONFIG = """{
    "propertySettings.phoneIMEI": "010306024934555",
    "propertySettings.phoneModel": "MI 9",
    "propertySettings.macAddress": "00DB0F665BBE",
    "advancedSettings.cpuCount": 3,
    "advancedSettings.resolution": {"width": 900, "height": 1600},
    "advancedSettings.resolutionDpi": 400,
    "basicSettings.rootMode": true,
    "basicSettings.autoRotate": false
}"""


def ld_settings_console(tmp_path, config=None, rows=None, failure=None, index="0"):
    """造一份雷电实例配置目录，加一个只会记账的 console。

    `get_settings` 用真实实现（它只读文件，正是被测的那段）；`list_devices()`
    走 FakeLDConsole，免得真去 spawn 一个空文件当 exe；`_run` 记下命令并按
    `failure` 决定退出码（实测 `modify --cpu 5` 是 rc 4294966291 + `parameter
    error!`）。
    """
    bin_dir = tmp_path / "LDPlayer"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "ldconsole.exe"
    exe.write_text("")
    config_dir = bin_dir / "vms" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (config_dir / f"leidian{index}.config").write_text(config)

    class SettingsLDConsole(FakeLDConsole):
        def __init__(self):
            super().__init__(rows if rows is not None else [ld_row(index, 1111, 2222)])
            self.bin_path = str(exe)
            self.commands: list[list[str]] = []
            self.failure = failure

        def _run(self, cmd, shell=False, encoding=None, timeout=None):  # type: ignore[override]
            self.commands.append(list(cmd))
            if self.failure is not None and cmd[0] == self.failure[0]:
                exit_code, output = self.failure[1]
                return CmdResult(output, "", exit_code)
            return CmdResult("", "", 0)

    return SettingsLDConsole()


def test_ld_get_settings_reads_the_instance_config(tmp_path):
    console = ld_settings_console(tmp_path, config=LD_CONFIG)

    settings = console.get_settings("0")

    assert settings["propertySettings.phoneIMEI"] == "010306024934555"
    assert settings["advancedSettings.cpuCount"] == "3"
    assert all(isinstance(value, str) for value in settings.values())


def test_ld_get_settings_stringifies_booleans_like_mumu(tmp_path):
    """bool 要排 int 前面判，否则 True 会变成 "1" 而不是 "true"。"""
    settings = ld_settings_console(tmp_path, config=LD_CONFIG).get_settings("0")

    assert settings["basicSettings.rootMode"] == "true"
    assert settings["basicSettings.autoRotate"] == "false"


def test_ld_get_settings_flattens_the_nested_values(tmp_path):
    settings = ld_settings_console(tmp_path, config=LD_CONFIG).get_settings("0")

    assert settings["advancedSettings.resolution.width"] == "900"
    assert settings["advancedSettings.resolution.height"] == "1600"
    assert settings["advancedSettings.resolutionDpi"] == "400"


def test_ld_get_settings_says_so_when_there_is_no_config(tmp_path):
    """返回空 dict 会让调用方以为「这台机器没有设置」—— 必须报错。"""
    console = ld_settings_console(tmp_path, config=None)

    with pytest.raises(RuntimeError, match="读不到雷电实例 0 的配置"):
        console.get_settings("0")


def test_ld_set_settings_passes_the_modify_flags(tmp_path):
    console = ld_settings_console(tmp_path, config=LD_CONFIG)

    console.set_settings("0", cpu="2", memory="4096", root="1")

    assert console.commands == [
        ["modify", "--index", "0", "--cpu", "2", "--memory", "4096", "--root", "1"]
    ]


def test_ld_set_settings_refuses_a_key_the_vendor_would_silently_ignore(tmp_path):
    """实测 `modify --bogus 1` 是 rc 0、stdout 空、什么都不改 —— 必须自己拦。"""
    console = ld_settings_console(tmp_path, config=LD_CONFIG)

    with pytest.raises(ValueError, match="不认识这些 key"):
        console.set_settings("0", bogus="1")

    assert console.commands == []


def test_ld_set_settings_reports_the_vendors_parameter_error(tmp_path):
    """实测 `modify --cpu 5` → rc 4294966291 + stdout `parameter error!`。

    这是本库唯一能用退出码判成败的地方：`add` 成功时退出码就是新 index，
    所以那三个动词一律靠回读 list2。
    """
    console = ld_settings_console(
        tmp_path, config=LD_CONFIG, failure=("modify", (4294966291, "parameter error!"))
    )

    with pytest.raises(RuntimeError, match="parameter error"):
        console.set_settings("0", cpu="5")


def test_ld_set_settings_checks_the_instance_exists_first(tmp_path):
    """实测 `modify --index 99` 也是 rc 0、什么都不改 —— 先确认实例在。"""
    console = ld_settings_console(tmp_path, config=LD_CONFIG, rows=[])

    with pytest.raises(RuntimeError, match="没有 index 为 0 的实例"):
        console.set_settings("0", cpu="2")

    assert console.commands == []


def test_ld_set_settings_does_not_stop_a_running_instance(tmp_path):
    """实测雷电的 modify 在运行中也是 rc 0 且配置立即落盘，不必先停机。"""
    console = ld_settings_console(tmp_path, config=LD_CONFIG)
    assert console.probe_state("0") is DeviceStatus.BOOT

    console.set_settings("0", cpu="2")

    assert [call[0] for call in console.commands] == ["modify"]


def test_ld_read_and_write_speak_different_dialects(tmp_path):
    """读是配置字段名、写是 modify 参数名 —— 雷电只有写有 CLI，这是它的形状。

    锁住这个不对称，免得有人「顺手统一」成读得出的名字，然后写入静默失败。
    """
    console = ld_settings_console(tmp_path, config=LD_CONFIG)

    assert "advancedSettings.cpuCount" in console.get_settings("0")
    console.set_settings("0", cpu="2")
    assert console.commands == [["modify", "--index", "0", "--cpu", "2"]]


def test_settings_are_soft_members_of_the_interface():
    """没实现的厂商抛 NotImplementedError，不静默失败、不返回假值。"""
    bare = MumuConsole.__new__(MumuConsole)

    with pytest.raises(NotImplementedError, match="没有实现 get_settings"):
        EmulatorConsole.get_settings(bare, "0")

    with pytest.raises(NotImplementedError, match="没有实现 set_settings"):
        EmulatorConsole._write_settings(bare, "0", {"cpu": "2"})


