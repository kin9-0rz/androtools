import pytest
from androtools.android_sdk.emulator import Emulator
from androtools.android_sdk.platform_tools import ADB
from androtools.core.device import Device, DeviceInfo, DeviceManager


@pytest.fixture
def device():
    device_names = ADB().get_devices()
    if device_names is None:
        return
    assert isinstance(device_names, list)
    assert len(device_names) >= 1

    di = DeviceInfo(
        name="Pixel_XL_API_30",
        serial="emulator-5554",
    )

    return Device(di)


def test_ls(device: Device):
    if device is None:
        return
    output = device.ls("/")
    assert "data" in output
    assert "system" in output


def test_ps(device: Device):
    if device is None:
        return
    output = device.ps()
    assert "zygote" in output


def test_pidof(device: Device):
    if device is None:
        return
    output = device.pidof("zygote")
    assert type(output) == int
    output = device.pidof("error-test")
    assert output is None


def test_device_manager():
    dm = DeviceManager()
    if dm.get_total() == 0:
        return

    device = dm.get_free_device()
    output = device.ls("/")
    assert "data" in output

    output = device.ps()
    assert "zygote" in output
