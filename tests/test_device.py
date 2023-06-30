import pytest

from androtools.android_sdk.platform_tools import ADB
from androtools.core.device import Device


@pytest.fixture
def device():
    device_names, _ = ADB().get_devices()
    assert isinstance(device_names, list)
    assert len(device_names) >= 1
    return Device(device_names[0])


def test_ls(device: Device):
    output = device.ls("/")
    assert "data" in output
    assert "system" in output


def test_ps(device: Device):
    output = device.ps()
    assert "zygote" in output


def test_pidof(device: Device):
    output = device.pidof("zygote")
    assert type(output) == int
    output = device.pidof("error-test")
    assert output is None
