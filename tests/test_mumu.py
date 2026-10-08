"""MuMu 控制台的 characterization tests。

JSON fixture 全部是从 D:\\Program Files\\Netease\\MuMu\\nx_main 实测粘回来的，
不是编的 —— MuMu 的 CLI 表面（JSON 输出、缺键、错误码）都没有文档可查。

只有 list_devices 这一层碰进程，所以替换它就够了；命令拼装和 JSON 解析走的
都是真实实现。
"""

import json
import sys

import pytest

import androtools.core.mumu as mumu_module
from helpers import make_emulator_info
from androtools.cmd import CMD
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

# 实测：MuMuManager.exe setting -v 0 -a —— 扁平 dict，值全是字符串，没有嵌套
SETTINGS = {
    "core_version": "6.8.2.0",
    "resolution_width": "540.000000",
    "resolution_height": "960.000000",
    "resolution_dpi": "240.000000",
    "resolution_width.custom": "540.000000",
    "performance_cpu.custom": "2",
    "performance_mem.custom": "1.750000",
    "root_permission": "true",
}

# 实测：中立动词要靠这些**只读元数据**自己值域校验（MuMu 越界时会静默夹到边界）。
# 列表尾巴的 `(best=N)` 是推荐值，不是可选值的一部分。
SETTINGS_WITH_RANGES = SETTINGS | {
    "resolution_width.min": "380",
    "resolution_width.max": "4096",
    "resolution_height.min": "380",
    "resolution_height.max": "4096",
    "resolution_dpi.min": "10",
    "resolution_dpi.max": "960",
    "performance_cpu.list": "[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16](best=4)",
    "performance_mem.list": (
        "[0.750000,1.000000,1.500000,1.750000,2.000000,3.000000,4.000000,5.000000,"
        "6.000000,7.000000,8.000000,9.000000,10.000000,11.000000,12.000000,13.000000,"
        "14.000000,15.000000,16.000000](best=6.000000)"
    ),
}

# 实测：写只读 key 的报错。注意它说的是「这个 key 只读」——
# 在**停机**实例上写只读 key 同样是 -101，与实例在不在跑无关。
KEY_NOT_WRITABLE = {"errcode": -101, "errmsg": "key not writable"}


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
                # 可调用值：用来模拟「命令执行后状态真的变了」
                # （例如 shutdown 之后 info 就该报 STOPPED）。
                value = payload() if callable(payload) else payload
                text = value if isinstance(value, str) else json.dumps(value)
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


# --------------------------------------------------------------------------- #
# create / clone / delete / rename —— MuMu：返回体按新 index 分键
# --------------------------------------------------------------------------- #

# 实测：MuMuManager.exe create --number 1 —— 返回体按新编号分键，顶层没有 errcode
CREATED_AT_2 = {"2": {"errcode": 0, "errmsg": ""}}


def test_mumu_create_returns_the_index_the_cli_reported():
    """新编号直接在返回值里，不用靠前后 diff。"""
    mumu = console(**{"create --number 1": CREATED_AT_2})

    assert mumu.create() == "2"
    assert mumu.calls == [["info", "-v", "all"], ["create", "--number", "1"]]


def test_mumu_create_renames_when_a_name_is_asked_for():
    """MuMu 的 create 不接受名字，所以「建完再改名」是两步。"""
    mumu = console(
        **{
            "create --number 1": CREATED_AT_2,
            "rename -v 2 --name": {"errcode": 0},
        }
    )

    assert mumu.create("新实例") == "2"
    assert mumu.calls[-1] == ["rename", "-v", "2", "--name", "新实例"]


def test_mumu_create_surfaces_the_error_hidden_in_the_response():
    """create 的错误藏在那台新实例的值里，顶层没有 errcode —— 必须逐键看。"""
    mumu = console(**{"create --number 1": {"2": {"errcode": -1, "errmsg": "disk full"}}})

    with pytest.raises(RuntimeError, match="disk full"):
        mumu.create()


def test_mumu_create_falls_back_to_diffing_the_instance_list():
    """返回值里没有可用的编号时，用前后 diff 兜底。"""
    state = {"created": False}

    def all_info():
        return {
            "0": STOPPED,
            "1": RUNNING,
            **({"7": STOPPED} if state["created"] else {}),
        }

    def create():
        state["created"] = True
        return {"oops": "not an index"}

    mumu = console(**{"info -v all": all_info, "create --number 1": create})

    assert mumu.create() == "7"


def test_mumu_create_reports_when_no_instance_showed_up():
    """两条路都失败就报错，**不能**返回一个猜的 index。"""
    mumu = console(**{"create --number 1": {"oops": "not an index"}})

    with pytest.raises(RuntimeError, match="没有出现新实例"):
        mumu.create()


def test_mumu_clone_returns_the_new_index():
    mumu = console(**{"clone -v 0 --number 1": {"2": {"errcode": 0}}})

    assert mumu.clone("0") == "2"
    assert mumu.calls[-1] == ["clone", "-v", "0", "--number", "1"]


def test_mumu_delete_of_a_stopped_instance_goes_straight_through():
    """机器已经停了就不多此一举去 shutdown。"""
    mumu = console(**{"info -v all": {"1": RUNNING}, "delete -v 0": {"errcode": 0}})

    mumu.delete("0")

    assert [c[0] for c in mumu.calls] == ["info", "delete", "info"]


def test_mumu_delete_shuts_a_running_instance_down_first():
    """实测：对运行中的实例直接 delete 会被拒（errcode -103
    `player is running, command not allowed`），所以要先把机器停干净。

    这也是「编排在 adapter 里」的价值：调用方只要说 delete。
    """
    state = {"running": True, "deleted": False}

    def info():
        return dict(RUNNING if state["running"] else STOPPED)

    def shutdown():
        state["running"] = False
        return {"errcode": 0}

    def delete():
        state["deleted"] = True
        return {"errcode": 0}

    def all_info():
        return {} if state["deleted"] else {"1": info()}

    mumu = console(
        **{
            "info -v all": all_info,
            "info -v 1": info,
            "control -v 1 shutdown": shutdown,
            "delete -v 1": delete,
        }
    )

    mumu.delete("1")

    assert [c[0] for c in mumu.calls] == ["info", "control", "info", "delete", "info"]
    assert state["running"] is False
    assert state["deleted"] is True


def test_mumu_delete_does_not_invent_its_own_timeout(monkeypatch):
    """停不下来时不自己造错误 —— 让厂商的 -103 报出来，那条信息更接近事实。"""
    monkeypatch.setattr(mumu_module, "_STOP_WAIT_SECONDS", 0.0)
    mumu = console(
        **{
            "delete -v 1": {
                "errcode": -103,
                "errmsg": "player is running, command not allowed",
            }
        }
    )

    with pytest.raises(RuntimeError, match="player is running"):
        mumu.delete("1")


def test_mumu_delete_surfaces_the_vendors_error():
    """实测：删不存在的索引给 errcode -201 `player not running`。"""
    mumu = console(**{"delete -v 9": {"errcode": -201, "errmsg": "player not running"}})

    with pytest.raises(RuntimeError, match="player not running"):
        mumu.delete("9")


def test_mumu_delete_says_so_when_the_instance_survives(monkeypatch):
    """命令说成功、实例其实还在 —— 不能就这么算了。"""
    monkeypatch.setattr(mumu_module, "_STOP_WAIT_SECONDS", 0.0)
    mumu = console(**{"delete -v 1": {"errcode": 0}})

    with pytest.raises(RuntimeError, match="仍在 info -v all 里"):
        mumu.delete("1")


def test_mumu_rename_passes_the_name_after_the_flag():
    mumu = console(**{"rename -v 0 --name": {"errcode": 0}})

    mumu.rename("0", "新名字")

    assert mumu.calls == [["rename", "-v", "0", "--name", "新名字"]]


def test_mumu_rename_surfaces_the_param_error():
    """实测：空名字给 -22，不给 `--name` 给 -21，文案都带 errcode 透出。"""
    mumu = console(
        **{"rename -v 0 --name": {"errcode": -22, "errmsg": "Missing param <name> value error !!!"}}
    )

    with pytest.raises(RuntimeError, match="Missing param <name> value error"):
        mumu.rename("0", "")


# --------------------------------------------------------------------------- #
# get_settings / set_settings —— 厂商方言的读写
# --------------------------------------------------------------------------- #


def test_mumu_get_settings_returns_the_flat_table():
    mumu = console(**{"setting -v 0 -a": SETTINGS})

    settings = mumu.get_settings("0")

    assert settings["performance_cpu.custom"] == "2"
    assert settings["root_permission"] == "true"
    assert all(isinstance(value, str) for value in settings.values())


def test_mumu_get_settings_is_one_console_call():
    mumu = console(**{"setting -v 0 -a": SETTINGS})

    mumu.get_settings("0")

    assert mumu.calls == [["setting", "-v", "0", "-a"]]


def test_mumu_set_settings_writes_every_key_in_one_call():
    mumu = console(**{"setting -v 0 -k": {"performance_cpu.custom": "2"}})

    mumu.set_settings("0", **{"performance_cpu.custom": "2", "root_permission": "true"})

    assert mumu.calls == [
        [
            "setting",
            "-v",
            "0",
            "-k",
            "performance_cpu.custom",
            "-val",
            "2",
            "-k",
            "root_permission",
            "-val",
            "true",
        ]
    ]


def test_mumu_set_settings_surfaces_the_vendor_error():
    """写只读 key 会被拒 —— 报错要透出厂商的 errcode 文案。"""
    mumu = console(**{"setting -v 0 -k": KEY_NOT_WRITABLE})

    with pytest.raises(RuntimeError, match="key not writable"):
        mumu.set_settings("0", resolution_width="720")


def test_mumu_set_settings_does_not_stop_a_running_instance():
    """「只有停机才能写」是实测推翻的误解：运行中的实例写可写 key 完全成功。

    所以这里**不该**出现任何 control/shutdown —— 白关一次别人的模拟器，
    比「值到下次启动才生效」是更坏的意外。
    """
    mumu = console(**{"setting -v 1 -k": {"performance_cpu.custom": "2"}})

    mumu.set_settings("1", **{"performance_cpu.custom": "2"})

    assert [call[0] for call in mumu.calls] == ["setting"]


def test_set_settings_refuses_an_empty_call():
    """空调用不是合法请求 —— 报 ValueError，而不是发一条什么都不写的命令。"""
    mumu = console()

    with pytest.raises(ValueError, match="至少要给一个 key=value"):
        mumu.set_settings("0")

    assert mumu.calls == []


# --------------------------------------------------------------------------- #
# 中立配置动词 —— 语义对齐的 set_resolution / set_cpu / set_memory / set_root
# --------------------------------------------------------------------------- #


def ranged(**overrides):
    """带只读值域元数据的实例，供中立动词值域校验用。"""
    responses = {
        "setting -v 0 -a": SETTINGS_WITH_RANGES,
        "setting -v 0 -k": {"errcode": 0},
    }
    responses.update(overrides)
    return console(**responses)


def test_mumu_set_resolution_writes_the_mode_and_three_customs_in_one_call():
    """`resolution_mode=custom` 必须与三个 `.custom` 在**同一次**调用里写完。"""
    mumu = ranged()

    mumu.set_resolution("0", 540, 960, 240)

    assert mumu.calls == [
        ["setting", "-v", "0", "-a"],
        [
            "setting",
            "-v",
            "0",
            "-k",
            "resolution_mode",
            "-val",
            "custom",
            "-k",
            "resolution_width.custom",
            "-val",
            "540",
            "-k",
            "resolution_height.custom",
            "-val",
            "960",
            "-k",
            "resolution_dpi.custom",
            "-val",
            "240",
        ],
    ]


def test_mumu_set_resolution_refuses_a_width_the_vendor_would_silently_clamp():
    """实测宽 100 → **rc 0**，只是回读变成 380。所以必须在发命令之前拦。"""
    mumu = ranged()

    with pytest.raises(ValueError, match="380..4096"):
        mumu.set_resolution("0", 100, 960, 240)

    assert [call[0] for call in mumu.calls] == ["setting"], "只该读元数据，不该有写入"
    assert mumu.calls == [["setting", "-v", "0", "-a"]]


def test_mumu_set_resolution_refuses_a_dpi_the_vendor_would_silently_clamp():
    """实测 dpi 5000 → rc 0，只是回读变成 960。"""
    mumu = ranged()

    with pytest.raises(ValueError, match="10..960"):
        mumu.set_resolution("0", 540, 960, 5000)

    assert mumu.calls == [["setting", "-v", "0", "-a"]]


def test_mumu_set_resolution_reads_the_range_from_the_machine_not_from_a_constant():
    """值域得**现读**：同一份代码在别的机器上边界不同，写死会在那里过期。"""
    narrow = SETTINGS_WITH_RANGES | {"resolution_width.max": "720"}
    mumu = ranged(**{"setting -v 0 -a": narrow})

    with pytest.raises(ValueError, match="380..720"):
        mumu.set_resolution("0", 1080, 960, 240)


def test_mumu_set_cpu_writes_the_mode_and_the_core_count():
    mumu = ranged()

    mumu.set_cpu("0", 8)

    assert mumu.calls[-1] == [
        "setting",
        "-v",
        "0",
        "-k",
        "performance_mode",
        "-val",
        "custom",
        "-k",
        "performance_cpu.custom",
        "-val",
        "8",
    ]


def test_mumu_set_cpu_refuses_a_count_outside_the_machines_list():
    """实测 32 是 `-105 cpu setting not found in list` —— 那句话说不出哪个值行。"""
    mumu = ranged()

    with pytest.raises(ValueError, match="只能是"):
        mumu.set_cpu("0", 32)

    assert mumu.calls == [["setting", "-v", "0", "-a"]]


def test_mumu_set_memory_converts_megabytes_to_the_vendors_gigabytes():
    """中立层收 MB，MuMu 存 GB（实测 `1.750000` 这种六位小数字符串）。"""
    mumu = ranged()

    mumu.set_memory("0", 2048)

    assert mumu.calls[-1] == [
        "setting",
        "-v",
        "0",
        "-k",
        "performance_mode",
        "-val",
        "custom",
        "-k",
        "performance_mem.custom",
        "-val",
        "2.000000",
    ]


def test_mumu_set_memory_snaps_to_the_nearest_slot():
    """实测只有 0.75/1/1.5/1.75/2/3…16 GB 这些档 —— 1000 MB 落在 0.75 与 1 之间。"""
    mumu = ranged()

    mumu.set_memory("0", 1000)

    assert "performance_mem.custom" in mumu.calls[-1]
    assert mumu.calls[-1][-1] == "1.000000"


def test_mumu_set_memory_refuses_a_value_below_the_smallest_slot():
    """100 MB 比最小档（0.75 GB）还小 —— 不静默夹到 0.75，那等于给人多要了 7 倍内存。"""
    mumu = ranged()

    with pytest.raises(ValueError, match="内存"):
        mumu.set_memory("0", 100)

    assert mumu.calls == [["setting", "-v", "0", "-a"]]


def test_mumu_set_root_writes_a_string_boolean():
    mumu = ranged(**{"setting -v 0 -k": {"root_permission": "false"}})

    mumu.set_root("0", False)

    assert mumu.calls[-1] == [
        "setting",
        "-v",
        "0",
        "-k",
        "root_permission",
        "-val",
        "false",
    ]


def test_mumu_set_root_writes_true_when_asked():
    mumu = ranged(**{"setting -v 0 -k": {"root_permission": "true"}})

    mumu.set_root("0", True)

    assert mumu.calls[-1] == [
        "setting",
        "-v",
        "0",
        "-k",
        "root_permission",
        "-val",
        "true",
    ]


# --------------------------------------------------------------------------- #
# 设备信息模拟 —— get_simulation / set_simulation（MuMu 是「双后端」）
# --------------------------------------------------------------------------- #

# 实测 `simulation -v <idx>`（不带 -sk）**只回设过的项**，新建实例回干净的空 dict。
SIMULATION = {"android_id": "0123456789abcdef", "mac_address": "00DB48FD6270"}

# 机型两项不在 simulation 里，只能从 setting 读。
PHONE_SETTINGS = SETTINGS | {"phone_miit": "22041211A", "phone_brand": "OPPO"}


def sim_console(**overrides):
    """带 simulation 与 phone_* 的实例。

    具体的写入模式要排在 `"simulation -v 0"` 前面 —— ScriptedMumuConsole 是
    「先插入的先命中」的子串匹配，顺序反了读的模式会把写也吃掉。
    """
    responses = {
        "simulation -v 0 -sk mac_address -sv": {"mac_address": "00DB48FD6270"},
        "simulation -v 0 -sk imei -sv": {"imei": "865166023949731"},
        "simulation -v 0 -sk android_id -sv": {"android_id": "0123456789abcdef"},
        "simulation -v 0 -sk": {"errcode": 0},
        "simulation -v 0": SIMULATION,
        "setting -v 0 -a": PHONE_SETTINGS,
        "setting -v 0 -k": {"errcode": 0},
    }
    responses.update(overrides)
    return console(**responses)


def test_mumu_get_simulation_merges_the_two_backends():
    """3 个 key 来自 `simulation`，机型两项来自 `setting`。"""
    mumu = sim_console()

    simulation = mumu.get_simulation("0")

    assert simulation["android_id"] == "0123456789abcdef"
    assert simulation["mac"] == "00DB48FD6270"  # 方言名是 mac_address
    assert simulation["model"] == "22041211A"  # 方言名是 phone_miit
    assert simulation["brand"] == "OPPO"  # 方言名是 phone_brand


def test_mumu_get_simulation_fills_the_unset_ones_with_empty():
    """没设过的项要补空串 —— `simulation` 读全部时根本不列它们。"""
    mumu = sim_console(**{"simulation -v 0": {}})

    simulation = mumu.get_simulation("0")

    assert set(simulation) == {"android_id", "imei", "mac", "model", "brand"}
    assert simulation["mac"] == ""
    assert simulation["imei"] == ""
    # 机型那两项总是有值：MuMu 的 phone_brand / phone_miit 是**真实默认值**，
    # 不是「覆盖表」里的一项
    assert simulation["model"] == "22041211A"


def test_mumu_get_simulation_is_three_console_calls_and_no_adb():
    """锁住形状：一次 info（查编号存在）+ simulation + setting -a。"""
    mumu = sim_console()

    mumu.get_simulation("0")

    assert mumu.calls == [
        ["info", "-v", "0"],
        ["simulation", "-v", "0"],
        ["setting", "-v", "0", "-a"],
    ]


def test_mumu_get_simulation_refuses_an_index_that_does_not_exist():
    """`simulation -v 99` 是 rc 0 加一个空值 —— 不自己查就报不出「这台不存在」。"""
    mumu = sim_console()

    with pytest.raises(RuntimeError, match="Missing param"):
        mumu.get_simulation("9")


def test_mumu_set_simulation_writes_mac_through_the_simulation_subcommand():
    mumu = sim_console()

    mumu.set_simulation("0", "mac", "00DB48FD6270")

    assert mumu.calls[-1] == [
        "simulation",
        "-v",
        "0",
        "-sk",
        "mac_address",
        "-sv",
        "00DB48FD6270",
    ]


def test_mumu_set_simulation_sends_model_to_the_setting_subcommand():
    """`model` 不在 `simulation` 里 —— 它归 `setting` 的 phone_miit。"""
    mumu = sim_console()

    mumu.set_simulation("0", "model", "SM-A5560")

    assert mumu.calls[-1] == ["setting", "-v", "0", "-k", "phone_miit", "-val", "SM-A5560"]


def test_mumu_set_simulation_sends_brand_to_phone_brand():
    mumu = sim_console()

    mumu.set_simulation("0", "brand", "OPPO")

    assert mumu.calls[-1] == ["setting", "-v", "0", "-k", "phone_brand", "-val", "OPPO"]


def test_mumu_set_simulation_rejects_a_key_the_vendor_would_happily_answer():
    """实测 `simulation -sk bogus_key` 回的是 rc 0 的 `{"bogus_key": ""}`。

    厂商对不认识的 key 毫无意见，所以「key 认不认识」只能中立层自己拦。
    """
    mumu = sim_console()

    with pytest.raises(ValueError, match="不认识模拟字段"):
        mumu.set_simulation("0", "serialno", "123")

    assert mumu.calls == []


def test_mumu_set_simulation_refuses_an_index_that_does_not_exist():
    mumu = sim_console()

    with pytest.raises(RuntimeError, match="Missing param"):
        mumu.set_simulation("9", "mac", "00DB48FD6270")

    assert not any(call[0] == "simulation" and "-sv" in call for call in mumu.calls)


# --------------------------------------------------------------------------- #
# play() —— 清单到可操作对象的通路
# --------------------------------------------------------------------------- #


def _console_pointing_at(tmp_path, name: str = "MuMuManager.exe") -> MumuConsole:
    """把 fake console 的 bin_path 指进 tmp_path，并在它旁边放一个空的 adb.exe。

    厂商自带的 adb 就在 Console 旁边（`bundled_adb_path`），放个空文件进去，
    play() 构造真 ADB 时就不会去找 PATH 上的 adb —— 那会让用例依赖机器。
    """
    (tmp_path / "adb.exe").write_text("")
    mumu = console()
    mumu.bin_path = str(tmp_path / name)
    return mumu


def test_mumu_play_builds_a_mumu_player_without_touching_the_console(tmp_path):
    mumu = _console_pointing_at(tmp_path)
    instance = mumu.list_instances()[1]
    mumu.calls.clear()

    player = mumu.play(instance)

    assert isinstance(player, MumuPlayer)
    assert player.index == "1"
    assert player.console is mumu
    assert mumu.calls == [], "play() 只构造，不该产生 IO"


def test_play_is_a_soft_member_of_the_interface():
    bare = MumuConsole.__new__(MumuConsole)

    with pytest.raises(NotImplementedError, match="没有实现 play"):
        EmulatorConsole.play(bare, make_emulator_info())


# --------------------------------------------------------------------------- #
# run() —— 逃生舱
# --------------------------------------------------------------------------- #


class _RealConsole(MumuConsole):
    """真的会 fork 子进程的 console，专门用来验 `run()` 的两条局限。

    `bin_path` 指向 python 自己 —— 不用装 MuMuManager 就能验「非零退出码不抛」
    和「按 adapter 声明的编码解」，比给 ScriptedMumuConsole 再加一层假退出码
    更接近真实路径。
    """

    def __init__(self) -> None:
        CMD.__init__(self, sys.executable)
        self.encoding = "utf-8"


def test_mumu_run_passes_the_arguments_through_without_parsing():
    """逃生舱给的是原始文本，不是解析过的 dict。"""
    mumu = console()

    result = mumu.run("info", "-v", "all")

    assert mumu.calls == [["info", "-v", "all"]]
    assert json.loads(result.output) == ALL


def test_run_does_not_raise_when_the_command_fails():
    """沿用 `CMD._run` 的现状：不看返回码，失败也照常返回。"""
    real = _RealConsole()

    result = real.run("-c", "import sys; sys.stderr.write('boom'); sys.exit(3)")

    assert result.exit_code == 3
    assert result.has_error()
    assert "boom" in result.error


def test_run_decodes_with_the_encoding_the_adapter_declared():
    """MuMu 的 stdout 是 UTF-8，与本机 locale 无关 —— 逃生舱也吃这个默认。"""
    real = _RealConsole()

    result = real.run(
        "-c", "import sys; sys.stdout.buffer.write('测试'.encode('utf-8'))"
    )

    assert result.output == "测试"
