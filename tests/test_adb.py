import pytest
from androtools.android_sdk.platform_tools import ADB

pytestmark = pytest.mark.integration


@pytest.fixture
def adb():
    return ADB()


def test_run_cmd(adb):
    result = adb.run_cmd(["devices"])
    assert "List of devices attached" in result.output
    assert result.contain("List of devices attached")


def test_run_shell_cmd(adb: ADB):
    result = adb.run_shell_cmd(["ps"])
    if result.contain("more than one device/emulator"):
        pytest.skip("adb 上连接了多个设备，get-state 语义不明确")
    assert result.contain("zygote")


def test_get_devices(adb: ADB):
    devices = adb.get_devices()
    assert len(devices) >= 1


def test_connect(adb):
    adb.connect("127.0.0.1", 5555)
