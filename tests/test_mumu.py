"""MuMu 控制台的 characterization tests。

JSON fixture 全部是从 D:\\Program Files\\Netease\\MuMu\\nx_main 实测粘回来的，
不是编的 —— MuMu 的 CLI 表面（JSON 输出、缺键、错误码）都没有文档可查。

只有 list_devices 这一层碰进程，所以替换它就够了；命令拼装和 JSON 解析走的
都是真实实现。
"""

import json

import pytest

from helpers import make_emulator_info
from androtools.cmd.result import CmdResult
from androtools.core.constants import KeyEvent
from androtools.core.device import EmulatorConsole, DeviceStatus, EmulatorInfo, EmulatorInstance
from androtools.core.mumu import MumuConsole, MumuPlayer
from androtools.testing import FakeADB

# 实测：MuMuManager.exe info -v 1
RUNNING = {
    "adb_host_ip": "127.0.0.1",
    "adb_port": 16416,
    "android_version": "12.0",
    "index": "1",
    "is_android_started": True,
    "is_process_started": True,
    "name": "A12",
    "pid": 10572,
    "player_state": "start_finished",
}

# 实测：MuMuManager.exe info -v 0 —— 未启动的实例把这些键整个省略了
STOPPED = {
    "android_version": "15.0",
    "index": "0",
    "is_main": False,
    "is_process_started": False,
    "name": "A15",
}

# 实测：MuMuManager.exe info -v <不存在的索引>
NO_SUCH_INSTANCE = {"errcode": -21, "errmsg": "Missing param <vmindex> error !!!"}

# 实测：MuMuManager.exe info -v all —— 键是字符串形式的 index，身下的值与 info -v <idx> 一样
ALL = {"0": STOPPED, "1": RUNNING}

# 实测：实例未启动时 sh 子命令的输出 —— 是 JSON 错误块，不是属性值
VM_NOT_RUNNING = {
    "errcode": -201,
    "errmsg": "vm not running, can not connect NemuShell !!",
}


class ScriptedMumuConsole(MumuConsole):
    """把 MuMuManager 的输出按命令脚本化，其余全部走真实实现。"""

    def __init__(self, responses: dict[str, object]):
        # 不调 super().__init__：那会去 shutil.which 找 MuMuManager.exe。
        # 但 bin_path 得给个确定的值 —— list_instances() 要用它拼 EmulatorInfo。
        self.bin_path = "MuMuManager.exe"
        self.responses = responses
        self.calls: list[list[str]] = []

    def _run(self, cmd, shell=False, encoding=None, timeout=None) -> CmdResult:  # type: ignore[override]
        self.calls.append(cmd)
        key = " ".join(cmd)
        for pattern, payload in self.responses.items():
            if pattern in key:
                text = payload if isinstance(payload, str) else json.dumps(payload)
                return CmdResult(text, "")
        return CmdResult(json.dumps({"errcode": -1, "errmsg": f"unexpected {key}"}), "")


def console(**overrides) -> ScriptedMumuConsole:
    responses: dict[str, object] = {
        "info -v all": ALL,
        "info -v 1": RUNNING,
        "info -v 0": STOPPED,
        "info -v 9": NO_SUCH_INSTANCE,
        "sh -v 1 -c getprop ro.build.version.sdk": "32",
        # 具体的键要排在通用模式前面，否则 "sh -v 1 -c getprop" 会先命中
        "sh -v 1 -c getprop sys.boot_completed": "1",
        "sh -v 1 -c getprop": "[ro.product.model]: [A12]",
        "sh -v 0 -c getprop ro.build.version.sdk": VM_NOT_RUNNING,
        "control -v 1 app launch": {"errcode": 0},
        "control -v 1 app close": {"errcode": 0},
        "control -v 1 launch": {"errcode": 0},
        "control -v 1 restart": {"errcode": 0},
        "control -v 1 shutdown": {"errcode": 0},
    }
    responses.update(overrides)
    return ScriptedMumuConsole(responses)


def make_player(mumu: MumuConsole, index: str = "1", adb=None) -> MumuPlayer:
    info = make_emulator_info(
        index=index,
        serial=None,
        name="A12",
        console_path="MuMuManager.exe",
    )
    return MumuPlayer(
        info, adb=FakeADB() if adb is None else adb, sleeper=lambda _s: None, console=mumu
    )


# --------------------------------------------------------------------------- #
# probe_state —— 用 MuMu 直接给的状态，不靠 PID 推断
# --------------------------------------------------------------------------- #


def test_probe_state_boot_when_the_player_is_running():
    assert console().probe_state("1") == DeviceStatus.BOOT


def test_probe_state_stop_when_the_player_is_not_running():
    assert console().probe_state("0") == DeviceStatus.STOP


def test_probe_state_stop_when_the_process_died_mid_boot():
    """进程起来了但 Android 还没起来 —— 只看进程会误判成已启动。"""
    booting = dict(RUNNING, is_android_started=False, player_state="booting")

    assert console(**{"info -v 1": booting}).probe_state("1") == DeviceStatus.BOOT


# --------------------------------------------------------------------------- #
# serial —— 从 adb_host_ip + adb_port 拼出来
# --------------------------------------------------------------------------- #


def test_serial_joins_host_and_port():
    assert console().serial("1") == "127.0.0.1:16416"


def test_serial_is_unavailable_before_the_player_starts():
    """未启动时 MuMu 省略 adb_host_ip / adb_port 两个键。"""
    with pytest.raises(RuntimeError, match="还没启动"):
        console().serial("0")


def test_refresh_serial_writes_into_device_info():
    mumu = console()
    player = make_player(mumu)

    player.refresh_serial()

    assert player.info.serial == "127.0.0.1:16416"
    # shell 每次现取 serial，所以改完立刻生效
    assert player.shell.serial == "127.0.0.1:16416"


# --------------------------------------------------------------------------- #
# getprop —— 成功是纯文本，失败是 JSON
# --------------------------------------------------------------------------- #


def test_getprop_returns_the_plain_value():
    assert console().getprop("1", "ro.build.version.sdk") == "32"


def test_getprop_without_a_key_returns_everything():
    assert console().getprop("1", None) == "[ro.product.model]: [A12]"


def test_getprop_raises_instead_of_returning_the_error_blob():
    """回归风险：sh 子命令出错时返回 JSON，不解析的话会把错误当成属性值。"""
    with pytest.raises(RuntimeError, match="vm not running"):
        console().getprop("0", "ro.build.version.sdk")


# --------------------------------------------------------------------------- #
# 命令拼装
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("launch_device", ["control", "-v", "1", "launch"]),
        ("reboot_device", ["control", "-v", "1", "restart"]),
        ("quit_device", ["control", "-v", "1", "shutdown"]),
    ],
)
def test_control_subcommands(method, expected):
    mumu = console()

    getattr(mumu, method)("1")

    assert mumu.calls == [expected]


def test_run_app_goes_through_control_app_launch():
    mumu = console()

    assert mumu.run_app("1", "com.example") is True
    assert mumu.calls == [
        ["control", "-v", "1", "app", "launch", "--package", "com.example"]
    ]


def test_kill_app_goes_through_control_app_close():
    mumu = console()

    mumu.kill_app("1", "com.example")

    assert mumu.calls == [
        ["control", "-v", "1", "app", "close", "--package", "com.example"]
    ]


def test_a_broken_command_does_not_silently_succeed():
    mumu = console()

    with pytest.raises(RuntimeError, match="Missing param"):
        mumu.probe_state("9")


def test_non_json_output_is_reported_rather_than_swallowed():
    mumu = console(**{"info -v 1": "not json at all"})

    with pytest.raises(RuntimeError, match="没有返回 JSON"):
        mumu.probe_state("1")


# --------------------------------------------------------------------------- #
# seam 一致性
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# ensure_connected —— MuMuManager 报的地址必须真的 connect 过才存在
# --------------------------------------------------------------------------- #

# 实测（两台 MuMu：index 0 = A15/Android 15，index 1 = A12/Android 12）：
# MuMu 会自己往 adb server 注册一个 emulator-555X，而 MuMuManager 报的
# 127.0.0.1:<adb_port> 默认并不在 adb devices 里 —— 要 connect 之后才出现。
# 而那个 emulator-555X 跟雷电抢同一个号段：MuMu index 1 占住了 emulator-5556，
# 雷电 index 0 先占住 emulator-5554，于是 MuMu index 0 只剩 tcp 地址可用。
# 所以只有 127.0.0.1:<adb_port> 是 MuMu 的权威地址。

CONNECTED = CmdResult("connected to 127.0.0.1:16416", "")


def test_mumu_connects_when_adb_has_never_seen_the_address():
    adb = FakeADB(responses={("connect", "127.0.0.1:16416"): CONNECTED}, devices=[])
    player = make_player(console(), adb=adb)

    assert player.ensure_connected() == "127.0.0.1:16416"
    assert adb.commands() == [("connect", "127.0.0.1:16416")]


def test_mumu_does_not_connect_when_adb_already_knows_the_address():
    """已经连着还去 connect 是白跑一趟进程。"""
    adb = FakeADB(
        responses={("connect", "127.0.0.1:16416"): CONNECTED},
        devices=[("127.0.0.1:16416", "device", "3")],
    )
    player = make_player(console(), adb=adb)

    assert player.ensure_connected() == "127.0.0.1:16416"
    assert adb.commands() == []


def test_mumu_resolves_the_serial_before_connecting():
    """serial 没给（默认就是 None）时要先向 Console 要地址。"""
    adb = FakeADB(responses={("connect", "127.0.0.1:16416"): CONNECTED}, devices=[])
    player = make_player(console(), adb=adb)

    player.ensure_connected()

    assert player.info.serial == "127.0.0.1:16416"


def test_mumu_connect_is_not_pinned_to_a_device():
    """connect 的对象是 adb server，不是某台设备 —— 不能带 -s。

    带了会出两种问题：serial 为空且多设备在线时先撞上 AmbiguousDeviceError；
    以及 -s 一个还没连上的地址，自相矛盾。
    """
    adb = FakeADB(responses={("connect", "127.0.0.1:16416"): CONNECTED}, devices=[])
    player = make_player(console(), adb=adb)
    player.refresh_serial()

    player.ensure_connected()

    assert [call.serial for call in adb.calls] == [None]


def test_mumu_connect_happens_even_with_several_devices_online():
    """多设备在线时这条命令依然要发出去 —— 这正是它存在的理由。"""
    adb = FakeADB(
        responses={("connect", "127.0.0.1:16416"): CONNECTED},
        devices=[("emulator-5554", "device", "1"), ("emulator-5556", "device", "2")],
    )
    player = make_player(console(), adb=adb)

    player.ensure_connected()

    assert adb.commands() == [("connect", "127.0.0.1:16416")]


def test_mumu_gives_up_quietly_when_the_instance_is_not_started():
    adb = FakeADB(devices=[])
    player = make_player(console(), index="0", adb=adb)

    with pytest.raises(RuntimeError, match="还没启动"):
        player.ensure_connected()


def test_mumu_get_status_connects_before_probing():
    """回归风险：从 MuMu 启动器外部启动的实例没人 connect 过，
    get_status() 会一路判到 ERORR —— 看着像设备坏了，其实只是没连上。"""
    adb = FakeADB(
        responses={
            ("connect", "127.0.0.1:16416"): CONNECTED,
            ("get-state",): CmdResult("device", ""),
            ("input", "keyevent", str(KeyEvent.KEYCODE_ALT_LEFT.value)): CmdResult(
                "", ""
            ),
        },
        devices=[("emulator-5554", "device", "1")],
    )
    player = make_player(console(), adb=adb)

    status = player.get_status()

    assert status == DeviceStatus.BOOT_COMPLETED
    assert adb.commands()[0] == ("connect", "127.0.0.1:16416")


def test_mumu_launch_still_connects_after_refreshing_the_serial():
    """launch 里的那次 connect 不能因为新方法而丢掉。"""
    adb = FakeADB(responses={("connect", "127.0.0.1:16416"): CONNECTED}, devices=[])
    player = make_player(console(), adb=adb)

    player.launch()

    assert player.info.serial == "127.0.0.1:16416"
    assert ("connect", "127.0.0.1:16416") in adb.commands()


def test_mumu_is_a_third_adapter_on_the_same_seams():
    assert issubclass(MumuConsole, EmulatorConsole)
    assert MumuConsole.__abstractmethods__ == frozenset()
    assert issubclass(MumuPlayer, type(make_player(console())))


def test_session_answers_is_boot_through_the_console():
    player = make_player(console())

    assert player.is_boot() is True
    assert player.index == "1"


# --------------------------------------------------------------------------- #
# list_instances —— 起点：有几台、哪台在跑
# --------------------------------------------------------------------------- #


def test_list_instances_reports_every_instance_including_the_stopped_one():
    """原来的问题就是「知道有几个设备、索引多少、是否在运行」。"""
    instances = console().list_instances()

    assert [i.index for i in instances] == ["0", "1"]
    assert [i.name for i in instances] == ["A15", "A12"]


def test_list_instances_tells_the_running_one_from_the_stopped_one():
    by_index = {i.index: i for i in console().list_instances()}

    assert by_index["1"].status == DeviceStatus.BOOT
    assert by_index["1"].is_running is True
    assert by_index["0"].status == DeviceStatus.STOP
    assert by_index["0"].is_running is False


def test_list_instances_gives_an_adb_address_only_for_running_instances():
    """未启动时 MuMu 把 adb_host_ip / adb_port 整个键省略了。

    回归风险：拿 16384 + 2*index 推算端口 —— 实测 index 0 占 16384、index 1 是 16416，
    那个公式在本机就是错的。
    """
    by_index = {i.index: i for i in console().list_instances()}

    assert by_index["1"].serial == "127.0.0.1:16416"
    assert by_index["0"].serial is None


def test_list_instances_normalises_the_android_version():
    """MuMu 报 "12.0"，而 Device.android_version 读的是 "12" —— 统一成后者。"""
    by_index = {i.index: i for i in console().list_instances()}

    assert by_index["1"].android_version == "12"
    assert by_index["0"].android_version == "15"


def test_list_instances_is_one_console_call_and_no_adb():
    """列实例不该逐 index 再问一次，也不该碰 adb。"""
    mumu = console()

    mumu.list_instances()

    assert mumu.calls == [["info", "-v", "all"]]


def test_list_instances_sorts_by_numeric_index():
    """`info -v all` 的键是字符串，不排序的话 "10" 会跑到 "9" 前面。"""
    mumu = console(**{"info -v all": {"10": STOPPED, "9": RUNNING}})

    assert [i.index for i in mumu.list_instances()] == ["9", "10"]


def test_list_instances_carries_what_a_player_needs_to_be_built():
    """快照里的 info 必须是可直接拿去构造 session 的那五个字段。"""
    instance = console().list_instances()[0]

    assert isinstance(instance.info, EmulatorInfo)
    assert instance.info.console_path == "MuMuManager.exe"
    assert instance.info.index == "0"
    assert instance.info.adb_path


def test_emulator_instance_equality_survives_a_changed_adb_port():
    """MuMu 重启后端口会变，那不该让「同一台实例」变成两台。"""
    before = EmulatorInstance(
        EmulatorInfo("A12", None, "adb", "1", "MuMuManager.exe"),
        DeviceStatus.STOP,
    )
    after = EmulatorInstance(
        EmulatorInfo("A12", "127.0.0.1:16416", "adb", "1", "MuMuManager.exe"),
        DeviceStatus.STOP,
    )

    assert before == after
    assert len({before, after}) == 1