import os

import pytest

from androtools.control.controller import DeviceController, DeviceStatus
from androtools.device import DeviceInfo
from androtools.device.phone import Phone

# import androtools

fixtures_path = os.path.join(os.path.dirname(__file__), "fixtures")

# androtools.enable_console_logging()


@pytest.fixture
def dc() -> DeviceController:
    info = DeviceInfo()
    phone = Phone(info)
    dc = DeviceController(phone)
    status = dc.get_status()
    if status != DeviceStatus.STOP:
        return dc
    raise Exception("模拟器没启动，或者手机没连接")


def test_app(dc: DeviceController):
    r = dc.app.get_3rd_party_apps()
    assert len(r) > 0
    r = dc.app.get_current_app()
    print(r)
    assert r == "Home"


def test_linux_command(dc: DeviceController):
    r = dc.linux_command.ps()
    assert len(r) > 0
    r = dc.env.show()
