"""AndroidShell 的 characterization tests。

只测有 implementation 的部分：sdk 的懒解析与缓存、install_app 的 SDK 分支、
run_app 的 activity 解析、list_packages 的输出转换、以及 serial 的动态读取。

像 tap / swipe / mkdir 这类纯参数拼接不测 —— 它们是 shallow pass-through，
测了等于把命令的字符形状钉死。
"""

import pytest

from helpers import make_device_info, make_emulator_info
from androtools.android_sdk.platform_tools import AmbiguousDeviceError
from androtools.cmd.result import CmdResult
from androtools.core.shell import AndroidShell
from androtools.testing import FakeADB

SERIAL = "127.0.0.1:5555"
SDK_PROP = ("getprop", "ro.build.version.sdk")


def make_shell(responses=None, errors=None, info=None) -> tuple[AndroidShell, FakeADB]:
    adb = FakeADB(responses=responses, errors=errors)
    return AndroidShell(adb, info or make_emulator_info()), adb


# --------------------------------------------------------------------------- #
# sdk
# --------------------------------------------------------------------------- #


def test_sdk_is_queried_lazily_and_then_cached():
    shell, adb = make_shell({SDK_PROP: CmdResult("30", "")})

    assert shell.sdk == 30
    assert shell.sdk == 30

    assert adb.count(*SDK_PROP) == 1


def test_sdk_is_minus_one_when_the_device_does_not_answer():
    """开机早期 getprop 可能返回空 —— 不能因此崩在 int() 上。"""
    shell, adb = make_shell({SDK_PROP: CmdResult("", "")})

    assert shell.sdk == -1


# --------------------------------------------------------------------------- #
# serial
# --------------------------------------------------------------------------- #


def test_serial_follows_the_info_object():
    """夜神在启动过程中会改写 DeviceInfo.serial，shell 必须每次现取。"""
    info = make_emulator_info()
    shell, _ = make_shell(info=info)

    assert shell.serial == SERIAL

    info.serial = "emulator-5554"

    assert shell.serial == "emulator-5554"


def test_adb_call_passes_the_current_serial():
    info = make_emulator_info()
    adb = FakeADB(responses={("get-state",): CmdResult("device", "")})
    shell = AndroidShell(adb, info)

    info.serial = "emulator-5554"
    shell.adb(["get-state"])

    assert adb.calls[0].serial == "emulator-5554"


# --------------------------------------------------------------------------- #
# serial 为空时的歧义保护
# --------------------------------------------------------------------------- #


class CountingDeviceQueries(FakeADB):
    """FakeADB 的 get_devices 是直接返回，不走 run_cmd，所以数不到调用次数。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.device_queries = 0

    def get_devices(self):
        self.device_queries += 1
        return super().get_devices()


def test_serial_is_required_when_several_devices_are_attached():
    """回归测试：serial 为空且多台在线时，命令会静默失败。

    adb 回 more than one device/emulator，而上层可能把这个失败当成「设备没启动」
    或者干脆忽略 —— 于是一个查不到任何东西的 Device 报出了 BOOT_COMPLETED。
    这条在真机 + MuMu + 雷电同时插着时真的发生过。
    """
    adb = FakeADB(
        responses={("get-state",): CmdResult("device", "")},
        devices=[("PHONE", "device", "1"), ("emulator-5554", "device", "2")],
    )
    shell = AndroidShell(adb, make_device_info(serial=None))

    with pytest.raises(AmbiguousDeviceError) as err:
        shell.adb(["get-state"])

    assert "emulator-5554" in str(err.value)
    assert "PHONE" in str(err.value)


def test_a_single_attached_device_may_be_relied_on_by_adb():
    """只有一台在线时，不加 -s 是安全的 —— adb 只会选它。"""
    adb = FakeADB(
        responses={("get-state",): CmdResult("device", "")},
        devices=[("ONLYONE", "device", "1")],
    )
    shell = AndroidShell(adb, make_device_info(serial=None))

    shell.adb(["get-state"])

    assert adb.calls[0].serial is None


def test_no_devices_attached_is_left_to_adb_to_report():
    """0 台在线时不用我们拦 —— adb 自己会回 no devices/emulators found。"""
    adb = FakeADB(responses={("get-state",): CmdResult("", "no devices/emulators found")})
    shell = AndroidShell(adb, make_device_info(serial=None))

    result = shell.adb(["get-state"])

    assert "no devices" in result.error


def test_the_success_verdict_is_cached_but_ambiguity_always_raises():
    """别让每条 adb 命令都多跑一次 devices -l —— 但歧义不能被缓存掉。

    缓存错误的话，第一次抛完异常之后第二次就静默放行，那正是这里要防的撒谎。
    """
    adb = FakeADB(
        responses={("get-state",): CmdResult("device", "")},
        devices=[("A", "device", "1"), ("B", "device", "2")],
    )
    shell = AndroidShell(adb, make_emulator_info(serial=None))

    with pytest.raises(AmbiguousDeviceError):
        shell.adb(["get-state"])

    adb.calls.clear()
    with pytest.raises(AmbiguousDeviceError):
        shell.adb(["get-state"])

    assert adb.calls == []  # 第二次没再查设备列表，但异常照抛


def test_the_single_device_verdict_is_cached():
    adb = CountingDeviceQueries(
        responses={("get-state",): CmdResult("device", "")},
        devices=[("ONLYONE", "device", "1")],
    )
    shell = AndroidShell(adb, make_emulator_info(serial=None))

    shell.adb(["get-state"])
    shell.adb(["get-state"])

    assert adb.device_queries == 1
    assert adb.commands("run_cmd") == [("get-state",), ("get-state",)]


def test_an_explicit_serial_skips_the_check_entirely():
    adb = FakeADB(
        responses={("get-state",): CmdResult("device", "")},
        devices=[("A", "device", "1"), ("B", "device", "2")],
    )
    shell = AndroidShell(adb, make_emulator_info(serial="B"))

    shell.adb(["get-state"])

    assert adb.calls[0].serial == "B"


# --------------------------------------------------------------------------- #
# install_app
# --------------------------------------------------------------------------- #


def test_install_app_grants_runtime_permissions_on_android_7_plus():
    shell, adb = make_shell(
        {
            SDK_PROP: CmdResult("30", ""),
            ("install", "-r", "-g", "-t", "app.apk"): CmdResult("Success", ""),
        }
    )

    ok, _ = shell.install_app("app.apk")

    assert ok is True
    assert adb.commands("run_cmd")[-1] == ("install", "-r", "-g", "-t", "app.apk")


def test_install_app_drops_runtime_permissions_on_android_6():
    """Android 7 以下不认 -g，带上会直接失败。"""
    shell, adb = make_shell(
        {
            SDK_PROP: CmdResult("23", ""),
            ("install", "-r", "-t", "app.apk"): CmdResult("Success", ""),
        }
    )

    ok, _ = shell.install_app("app.apk")

    assert ok is True
    assert adb.commands("run_cmd")[-1] == ("install", "-r", "-t", "app.apk")


def test_install_app_reports_failure():
    shell, _ = make_shell(
        {
            SDK_PROP: CmdResult("30", ""),
            ("install", "-r", "-g", "-t", "app.apk"): CmdResult(
                "Failure [INSTALL_FAILED_ALREADY_EXISTS]", ""
            ),
        }
    )

    ok, result = shell.install_app("app.apk")

    assert ok is False
    assert "INSTALL_FAILED" in result.output


# --------------------------------------------------------------------------- #
# run_app
# --------------------------------------------------------------------------- #

DUMPSYS_MAIN = """  Package [com.example] (a1b2c3):
    android.intent.action.MAIN:
      filter com.example/.MainActivity
    android.intent.action.MAIN:
"""


def test_run_app_starts_the_main_activity():
    shell, adb = make_shell(
        {
            ("dumpsys", "package", "com.example"): CmdResult(DUMPSYS_MAIN, ""),
            ("am", "start", "-n", "com.example/.MainActivity"): CmdResult("", ""),
        }
    )

    assert shell.run_app("com.example") is True
    assert adb.commands("run_shell_cmd")[-1] == ("am", "start", "-n", "com.example/.MainActivity")


def test_run_app_returns_false_without_a_main_activity():
    shell, _ = make_shell(
        {("dumpsys", "package", "com.example"): CmdResult("  Package [com.example]:", "")}
    )

    assert shell.run_app("com.example") is False


def test_run_app_returns_false_when_no_activity_name_can_be_parsed():
    """有 MAIN 条目但里面没有 类名/包名 形式 —— 不能瞎猜一个组件。"""
    shell, _ = make_shell(
        {
            ("dumpsys", "package", "com.example"): CmdResult(
                "  Package [com.example]:\n    android.intent.action.MAIN:\n", ""
            )
        }
    )

    assert shell.run_app("com.example") is False


# --------------------------------------------------------------------------- #
# list_packages
# --------------------------------------------------------------------------- #


def test_list_packages_strips_the_prefix():
    shell, _ = make_shell(
        {("pm", "list", "packages"): CmdResult("package:com.a\npackage:com.b\n", "")}
    )

    assert shell.list_packages() == ["com.a", "com.b"]


@pytest.mark.parametrize(
    ("flag", "extra"),
    [(0, "-3"), (1, "-s"), (-1, None)],
)
def test_list_packages_filters(flag, extra):
    cmd = ["pm", "list", "packages"]
    if extra:
        cmd.append(extra)
    shell, adb = make_shell({tuple(cmd): CmdResult("", "")})

    shell.list_packages(flag)

    assert adb.commands("run_shell_cmd")[-1] == tuple(cmd)


# --------------------------------------------------------------------------- #
# pidof
# --------------------------------------------------------------------------- #


def test_pidof_reads_the_pid_directly_when_available():
    shell, _ = make_shell({("pidof", "com.example"): CmdResult("4321", "")})

    assert shell.pidof("com.example") == 4321


def test_pidof_falls_back_to_parsing_ps_output():
    """老版本 Android 没有 pidof 命令 —— 回退到 ps 里找。"""
    shell, adb = make_shell(
        {
            ("pidof", "com.example"): CmdResult("/system/bin/sh: pidof: not found", ""),
            ("ps",): CmdResult(
                "USER PID PPID VSIZE RSS WCHAN PC NAME\n"
                "u0_a1 4321 1 100 200 ffffffff 0000000000 S com.example\n",
                "",
            ),
        }
    )

    assert shell.pidof("com.example") == 4321
    assert adb.count("ps") == 1


def test_pidof_returns_minus_one_when_the_process_is_gone():
    shell, _ = make_shell({("pidof", "com.example"): CmdResult("", "")})

    assert shell.pidof("com.example") == -1