import pytest

from androtools.android_sdk.platform_tools import ADB


@pytest.fixture
def adb():
    return ADB()


def test_run_cmd(adb):
    output, _ = adb.run_cmd(["devices"])
    assert "List of devices attached" in output


def test_run_shell_cmd(adb):
    output, _ = adb.run_shell_cmd(["ps"])
    assert "zygote" in output


def test_get_devices(adb):
    devices, _ = adb.get_devices()
    assert len(devices) >= 1


def test_connect(adb):
    adb.connect("127.0.0.1", 5555)
