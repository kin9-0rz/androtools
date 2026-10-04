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
from androtools.core.device import DeviceConsole, DeviceStatus
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

# 实测：实例未启动时 sh 子命令的输出 —— 是 JSON 错误块，不是属性值
VM_NOT_RUNNING = {
    "errcode": -201,
    "errmsg": "vm not running, can not connect NemuShell !!",
}


class ScriptedMumuConsole(MumuConsole):
    """把 MuMuManager 的输出按命令脚本化，其余全部走真实实现。"""

    def __init__(self, responses: dict[str, object]):
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
        "info -v 1": RUNNING,
        "info -v 0": STOPPED,
        "info -v 9": NO_SUCH_INSTANCE,
        "sh -v 1 -c getprop ro.build.version.sdk": "32",
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


def make_player(mumu: MumuConsole, index: str = "1") -> MumuPlayer:
    info = make_emulator_info(
        index=index,
        serial=None,
        name="A12",
        console_path="MuMuManager.exe",
    )
    return MumuPlayer(info, adb=FakeADB(), sleeper=lambda _s: None, console=mumu)


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


def test_mumu_is_a_third_adapter_on_the_same_seams():
    assert issubclass(MumuConsole, DeviceConsole)
    assert MumuConsole.__abstractmethods__ == frozenset()
    assert issubclass(MumuPlayer, type(make_player(console())))


def test_session_answers_is_boot_through_the_console():
    player = make_player(console())

    assert player.is_boot() is True
    assert player.index == "1"