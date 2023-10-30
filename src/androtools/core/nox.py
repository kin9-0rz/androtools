# 夜神模拟器
import shutil
from time import sleep

import psutil

from androtools.android_sdk import CMD
from androtools.android_sdk.platform_tools import ADB
from androtools.core.device import Device, DeviceConsole, DeviceInfo, DeviceStatus


class NoxConsole(DeviceConsole):
    def __init__(self, console_path=shutil.which("NoxConsole.exe")):
        super().__init__(console_path)

    def launch_device(self, idx: int | str):
        return self._run(["launch", f"-index:{idx}"])

    def reboot_device(self, idx: int | str):
        return self._run(["reboot", f"-index:{idx}"])

    def quit_device(self, idx: int | str):
        return self._run(["quit", f"-index:{idx}"])

    def quit_all_devices(self):
        return self._run(["quit-all"])

    def list_devices(self):
        """列出所有模拟器信息

        0. 索引
        1. 虚拟机名称
        1. 标题
        2. 顶层窗口句柄
        3. 工具栏窗口句柄
        5. Nox进程，父进程。
        6. 进程PID，NoxVMHandle Frontend；这个进程和adb连接。

        Returns:
            _type_: _description_
        """
        return self._run(["list"])


class NoxPlayerInfo(DeviceInfo):
    def __init__(
        self,
        index: str,
        serial: str | None,
        name: str,
        adb_path: str,
        console_path: str,
    ) -> None:
        super().__init__(index, serial, name, adb_path, console_path)


class NoxPlayer(Device):
    def __init__(self, info: DeviceInfo) -> None:
        super().__init__(info)

        self.index = info.index
        self.name = info.name
        self.nox_console = NoxConsole(info.console_path)

    def _init_serial(self):
        out, _ = self.nox_console.list_devices()
        pid = None
        for item in out.split("\n"):
            parts = item.split(",")
            if self.index != parts[0]:
                continue
            pid = int(parts[5])
            break

        if pid is None:
            raise ValueError("NoxPlayer not found")

        ports = set()
        net_con = psutil.net_connections()
        for con_info in net_con:
            if con_info.pid == pid and con_info.status == "LISTEN":
                ports.add(con_info.laddr.port)  # type: ignore

        out, _ = self._adb_wrapper.run_cmd(["devices", "-l"])
        for line in out.strip().split("\n"):
            if "List of devices attached" in line:
                continue
            parts = line.split()
            serial = parts[0]
            if int(serial.split(":")[-1]) in ports:
                self.serial = serial
                break

        if self.serial is None:
            raise ValueError("NoxPlayer could not find serial")

    def launch(self):
        self.nox_console.launch_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.RUN:
                break
            sleep(1)
        sleep(10)

    def close(self):
        self.nox_console.quit_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.STOP:
                break
            sleep(1)

    def reboot(self):
        self.nox_console.reboot_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.RUN:
                break
            sleep(1)
        sleep(10)

    def get_status(self):
        status = DeviceStatus.UNKNOWN
        out, _ = self.nox_console.list_devices()
        for line in out.strip().split("\n"):
            if self.name not in line:
                continue
            pid = line.split(",")[-1]
            if pid == "-1":
                status = DeviceStatus.STOP
            else:
                status = DeviceStatus.RUN
            break

        if status is DeviceStatus.RUN:
            if self.is_crashed():
                status = DeviceStatus.ERORR

        return status
