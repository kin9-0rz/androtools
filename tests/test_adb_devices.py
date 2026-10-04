"""ADB.get_devices 的 characterization tests。

以前这个方法每次迭代跑三次 devices -l（前两次结果丢弃），并且看到 127.0.0.1:
就去 restart_server —— 一个看起来纯查询的方法在改全局状态。现在是纯查询了，
于是可以完整覆盖，不需要真的插着设备。

run_cmd 就是 subprocess 上面的 seam，覆写它就能让真实的 get_devices 跑起来。
"""

import inspect

from androtools.android_sdk.platform_tools import ADB
from androtools.cmd.result import CmdResult

DEVICES = ("devices", "-l")

TWO_DEVICES = """List of devices attached
emulator-5554          device product:sdk_gphone_x86 model:Android_SDK device:generic_x86 transport_id:1
127.0.0.1:16416        device product:MuMu model:MuMu device:mu transport_id:2
"""

ONE_PHONE = """List of devices attached
ABC123XYZ             device product:redfin model:Pixel_5 device:redfin transport_id:9
"""

OFFLINE_AND_UNAUTHORIZED = """List of devices attached
emulator-5554          offline transport_id:1
ABC123XYZ             unauthorized transport_id:2
"""

NONE = "List of devices attached"

NO_SERVER = "List of devices attached\n* daemon not running; starting now at tcp:5037"


class ScriptedADB(ADB):
    """只替换 run_cmd（subprocess 上面的那个 seam），解析逻辑走真实实现。"""

    def __init__(self, output: str):
        self.output = output
        self.commands: list[tuple[str, ...]] = []
        self.server_restarts = 0

    def run_cmd(self, cmd, serial=None, timeout=30) -> CmdResult:
        self.commands.append(tuple(cmd))
        return CmdResult(self.output, "")

    def restart_server(self, force=True):
        self.server_restarts += 1


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def test_parses_serial_status_and_transport_id():
    assert ScriptedADB(TWO_DEVICES).get_devices() == [
        ("emulator-5554", "device", "1"),
        ("127.0.0.1:16416", "device", "2"),
    ]


def test_parses_a_physical_device():
    """真机的 serial 是一串字符，不是 host:port。"""
    assert ScriptedADB(ONE_PHONE).get_devices() == [("ABC123XYZ", "device", "9")]


def test_keeps_offline_and_unauthorized_as_statuses():
    """这两台设备都在，只是不可用 —— 该不该用是调用方的事。"""
    assert ScriptedADB(OFFLINE_AND_UNAUTHORIZED).get_devices() == [
        ("emulator-5554", "offline", "1"),
        ("ABC123XYZ", "unauthorized", "2"),
    ]


def test_empty_list_when_nothing_is_attached():
    assert ScriptedADB(NONE).get_devices() == []


def test_daemon_startup_noise_is_ignored():
    assert ScriptedADB(NO_SERVER).get_devices() == []


# --------------------------------------------------------------------------- #
# 纯查询：不再偷偷重启 adb server
# --------------------------------------------------------------------------- #


def test_asks_adb_exactly_once():
    """回归测试：以前每次迭代跑三次 devices -l，前两次的结果被丢弃。"""
    adb = ScriptedADB(TWO_DEVICES)

    adb.get_devices()

    assert adb.commands == [DEVICES]


def test_never_restarts_the_adb_server():
    """以前看到 127.0.0.1: 就 restart_server —— 改全局状态不该藏在一个查询里。"""
    adb = ScriptedADB(TWO_DEVICES)

    adb.get_devices()

    assert adb.server_restarts == 0


def test_no_max_tries_kwarg_anymore():
    """重试不再是它的职责；留着这个参数就是邀请别人继续拿它做重试。"""
    assert list(inspect.signature(ADB.get_devices).parameters) == ["self"]