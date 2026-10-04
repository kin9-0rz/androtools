"""真机的 characterization tests。

真机不需要 Console、没有 index、也没有「启动」这回事 —— 它插着就在那儿。所以
这里全部用 Device 基类本身，零 override。FakeADB 让这些断言不需要真机在线。
"""

import pytest
from helpers import make_device_info, make_emulator_info

from androtools.android_sdk.platform_tools import DeviceOfflineError
from androtools.cmd.result import CmdResult
from androtools.core.device import DeviceStatus, Pids
from androtools.core.session import Device, EmulatorSession
from androtools.testing import FakeADB

GET_STATE = ("get-state",)
BOOT_COMPLETED = ("getprop", "sys.boot_completed")
PROBE_KEY = ("input", "keyevent", "57")
RECONNECT = ("reconnect",)
VERSION = ("getprop", "ro.build.version.release")


def make_device(responses=None, errors=None, info=None) -> tuple[Device, FakeADB]:
    adb = FakeADB(responses=responses, errors=errors)
    return Device(info or make_device_info(), adb), adb


def online(boot_completed="1", state="device") -> tuple[Device, FakeADB]:
    return make_device(
        {
            GET_STATE: CmdResult(state, ""),
            RECONNECT: CmdResult("", ""),
            BOOT_COMPLETED: CmdResult(boot_completed, ""),
            PROBE_KEY: CmdResult("", ""),
        }
    )


def unplugged() -> tuple[Device, FakeADB]:
    return make_device({GET_STATE: CmdResult("", "device 'ABC123' not found")})


# --------------------------------------------------------------------------- #
# 真机不需要任何 subclass
# --------------------------------------------------------------------------- #


def test_a_real_device_is_the_base_class_itself():
    dev, _ = make_device()

    assert type(dev) is Device


def test_emulator_session_is_the_only_layer_that_needs_a_subclass():
    assert issubclass(EmulatorSession, Device)
    assert EmulatorSession.__abstractmethods__ == frozenset(
        {"launch", "close", "is_boot"}
    )


# --------------------------------------------------------------------------- #
# is_boot()：默认实现问 adb
# --------------------------------------------------------------------------- #


def test_is_boot_true_when_adb_answers():
    dev, adb = online()

    assert dev.is_boot() is True
    assert adb.commands("run_cmd") == [GET_STATE]


def test_is_boot_false_when_adb_cannot_find_the_device():
    """拔线 / 关 USB 调试，adb 回 device 'X' not found，且 stdout 为空。"""
    dev, _ = unplugged()

    assert dev.is_boot() is False


def test_is_boot_false_when_adb_has_no_devices_at_all():
    """回归测试：判据曾经是「输出里不含 not found」，这种文案就漏过去了。

    adb 的错误文案随版本变，`no devices/emulators found` 里根本没有 not found。
    """
    dev, _ = make_device({GET_STATE: CmdResult("", "error: no devices/emulators found")})

    assert dev.is_boot() is False


def test_is_boot_true_even_when_the_state_is_not_usable():
    """offline 表示连上了但不能操作 —— 那是 OFFLINE 状态，不是 STOP。"""
    dev, _ = make_device({GET_STATE: CmdResult("offline", "")})

    assert dev.is_boot() is True


# --------------------------------------------------------------------------- #
# get_status()：STOP 对真机 = adb 看不见它
# --------------------------------------------------------------------------- #


def test_status_is_stop_when_the_device_is_unplugged():
    dev, adb = unplugged()

    assert dev.get_status() == DeviceStatus.STOP
    assert adb.calls == [adb.calls[0]]  # 只有那次探测，没有后续的 reconnect / getprop


def test_status_boot_completed_for_a_healthy_device():
    dev, _ = online()

    assert dev.get_status() == DeviceStatus.BOOT_COMPLETED


def test_status_device_when_android_is_still_booting():
    dev, _ = online(boot_completed="0")

    assert dev.get_status() == DeviceStatus.DEVICE


def test_status_offline_when_adb_reports_offline_on_stdout():
    """offline 判据不依赖 adb 把它放在哪个流 —— contain 两个流都看。"""
    dev, _ = make_device(
        {GET_STATE: CmdResult("offline", ""), RECONNECT: CmdResult("", "")},
    )

    assert dev.get_status() == DeviceStatus.OFFLINE


def test_device_offline_is_not_mistaken_for_connected():
    """回归测试：output_equal 曾经是包含判断，「device offline」会被当成已连接。"""
    assert CmdResult("device offline", "").output_equal("device") is False


def test_a_device_adb_reports_nothing_about_is_stop_not_offline():
    """已验证：目标不存在时 adb 退出码非 0、stdout 为空。

    所以 stdout 空一律判 STOP —— 即使错误文案里有 offline。OFFLINE 只在 adb
    真的报出状态时才可达。
    """
    dev, _ = make_device({GET_STATE: CmdResult("", "error: device offline")})

    assert dev.get_status() == DeviceStatus.STOP


def test_status_error_when_the_device_stops_answering_mid_probe():
    """get-state 说 device，但按键探测抛异常 —— 判定为异常状态。"""
    dev, _ = make_device(
        {GET_STATE: CmdResult("device", ""), RECONNECT: CmdResult("", "")},
        errors={PROBE_KEY: RuntimeError("设备已断开")},
    )

    assert dev.get_status() == DeviceStatus.ERORR


# --------------------------------------------------------------------------- #
# 身份与展示
# --------------------------------------------------------------------------- #


def test_str_shows_name_and_serial_not_the_version():
    """打印对象不该有副作用 —— 版本是查出来的，取它要发一条 adb 命令。"""
    dev, adb = make_device()

    assert str(dev) == "test-device [127.0.0.1:5555]"
    assert adb.calls == []


def test_android_version_is_resolved_lazily_and_cached():
    dev, adb = make_device({VERSION: CmdResult("17", "")})

    assert dev.android_version == "17"
    assert dev.android_version == "17"
    assert adb.count(*VERSION) == 1


def test_android_version_comes_from_the_device_not_a_static_table():
    """回归测试：以前查本地的 Android_API_MAP，那张表停在 sdk 35。

    一台 sdk 37 的真机因此报 Unknown —— 而设备自己就知道答案。
    """
    dev, _ = make_device({VERSION: CmdResult("17", "")})

    assert dev.android_version == "17"


def test_android_version_is_unknown_when_the_device_does_not_answer():
    dev, _ = make_device({VERSION: CmdResult("", "")})

    assert dev.android_version == "Unknown"


def test_android_version_is_unknown_when_the_device_cannot_be_asked():
    """回归测试：问不到设备时曾经抛异常，而不是返回 Unknown。

    停掉的模拟器上 Console 会抛（MuMu: vm not running），拔线的真机会抛
    DeviceOfflineError —— 两者都是「问不到」，不是出错。
    """
    dev, _ = make_device({}, errors={VERSION: DeviceOfflineError("设备已断开")})

    assert dev.android_version == "Unknown"


def test_android_version_still_raises_on_a_real_failure():
    """只catch「问不到」那两种；其它异常照常冒出来。"""
    dev, _ = make_device({}, errors={VERSION: ValueError("解析不了")})

    with pytest.raises(ValueError):
        dev.android_version


def test_identity_is_serial_plus_adb_path():
    assert make_device_info(serial="ABC") == make_device_info(serial="ABC", name="改名了")
    assert make_device_info(serial="ABC") != make_device_info(serial="XYZ")
    assert make_device_info(serial="ABC", adb_path="a") != make_device_info(
        serial="ABC", adb_path="b"
    )


def test_comparing_with_a_non_device_is_false_not_an_error():
    """返回 NotImplemented 让 Python 落到「不相等」，而不是抛异常或意外为真。"""
    assert (make_device_info(serial="ABC") == "not a device") is False
    assert (make_device_info(serial="ABC") != 42) is True


def test_emulator_info_keeps_the_console_fields_but_inherits_the_identity_rule():
    """index / console_path 是模拟器独有的信息，不参与身份判定。"""
    assert make_emulator_info(index="3") == make_emulator_info(index="9")


def test_a_rewritten_serial_means_a_different_identity():
    """夜神和 MuMu 在启动流程里会改写 info.serial —— 改写前后是两个对象。

    想要「同一台设备的前后身份」得用 serial 之外的键自己维护。
    """
    before = make_device_info(serial="emulator-5554")
    after = make_device_info(serial="127.0.0.1:16416")

    assert before != after


def test_pids_is_running_requires_both_processes():
    """只看界面进程会把「半启动」误判成已启动 —— 雷电和夜神都栽在这。"""
    assert Pids(1111, 2222).is_running() is True
    assert Pids(-1, -1).is_running() is False
    assert Pids(1111, -1).is_running() is False
    assert Pids(-1, 2222).is_running() is False


def test_emulator_info_repr_shows_the_index():
    assert repr(make_emulator_info(index="2")) == "2 test-device"