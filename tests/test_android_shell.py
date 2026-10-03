"""AndroidShell 的 characterization tests。

只测有 implementation 的部分：sdk 的懒解析与缓存、install_app 的 SDK 分支、
run_app 的 activity 解析、list_packages 的输出转换、以及 serial 的动态读取。

像 tap / swipe / mkdir 这类纯参数拼接不测 —— 它们是 shallow pass-through，
测了等于把命令的字符形状钉死。
"""

from typing import Any

import pytest

from androtools.cmd.result import CmdResult
from androtools.core.device import DeviceInfo, DeviceType
from androtools.core.shell import AndroidShell
from androtools.testing import FakeADB

SERIAL = "127.0.0.1:5555"
SDK_PROP = ("getprop", "ro.build.version.sdk")


def make_info(**overrides: Any) -> DeviceInfo:
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


def make_shell(responses=None, errors=None, info=None) -> tuple[AndroidShell, FakeADB]:
    adb = FakeADB(responses=responses, errors=errors)
    return AndroidShell(adb, info or make_info()), adb


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
    info = make_info()
    shell, _ = make_shell(info=info)

    assert shell.serial == SERIAL

    info.serial = "emulator-5554"

    assert shell.serial == "emulator-5554"


def test_adb_call_passes_the_current_serial():
    info = make_info()
    adb = FakeADB(responses={("get-state",): CmdResult("device", "")})
    shell = AndroidShell(adb, info)

    info.serial = "emulator-5554"
    shell.adb(["get-state"])

    assert adb.calls[0].serial == "emulator-5554"


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