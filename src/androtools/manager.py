# Android模拟器、雷电模拟器的基类
from enum import Enum
from typing import Sequence

from androtools import logger
from androtools.device import Device
from androtools.control.controller import DeviceController


class WorkStatus(Enum):
    Free = 0
    Busy = 1


class DeviceManager:
    """
    只能管理 Android 同版的模拟器，不同版本，无法执行 adb。
    1. 根据已知设备初始化。
    2. 增加设备。
    3. 删除设备。
    """

    # 传入的不应该是信息？而是一个具体模拟器对象
    def __init__(self, devices: Sequence[Device]):
        self._devices: list[Device] = list(devices)
        self._device_map: dict[Device, WorkStatus] = {}
        self._device_map.clear()
        self.device_status = {}
        for dev in devices:
            logger.info(f"初始化设备 {dev.name}")
            dc = DeviceController(dev)
            status = dc.get_status()
            if dev.is_boot():
                continue
            dev.launch()
            # dev.launch_and_wait_for_device()

    def add(self, dev: Device):
        if not dev.is_boot():
            dev.launch()
        self._devices.append(dev)
        # dev.launch_and_wait_for_device()

    def remove(self, dev: Device):
        self._devices.remove(dev)

    def get_total(self) -> int:
        return len(self._devices)

    def get_free_device(self) -> Device | None:
        for dev in self._devices:
            if dev.is_busy:
                continue
            return dev
        return None

    def free_busy_device(self, device: Device):
        if device not in self._device_map:
            return
        self._device_map[device] = WorkStatus.Free
