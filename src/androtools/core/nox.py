# 夜神模拟器
import shutil
import time
from typing import Callable

import psutil

from androtools.android_sdk.platform_tools import AdbRunner
from androtools.cmd.result import CmdResult
from androtools.core.device import Device, DeviceConsole, DeviceInfo


class NoxConsole(DeviceConsole):
    def __init__(self, path=shutil.which("NoxConsole.exe")):
        super().__init__(path)

    def launch_device(self, idx: int | str):
        self._run(["launch", f"-index:{idx}"])
        time.sleep(3)

    def reboot_device(self, idx: int | str):
        self._run(["reboot", f"-index:{idx}"])
        time.sleep(3)

    def quit_device(self, idx: int | str):
        self._run(["quit", f"-index:{idx}"])
        time.sleep(3)

    def get_pids(self, idx: int | str) -> tuple[int, int]:
        """按 list 的列序取 PID：倒数第二列是 Nox.exe，倒数最后一列是 NoxVMHandle.exe。"""
        for line in self.list_devices().strip().split("\n"):
            parts = line.split(",")
            if parts[0] != str(idx):
                continue
            return int(parts[-2]), int(parts[-1])

        return -1, -1

    def list_devices(self) -> str:
        """列出所有模拟器信息

        0. 索引
        1. 虚拟机名称
        1. 标题
        2. 顶层窗口句柄
        3. 工具栏窗口句柄
        5. Nox.exe 模拟器进程
        6. NoxVMHandle.exe；这个进程和adb连接，它最先启动

        Returns:
            _type_: _description_
        """
        r = self._run(["list"])
        return r.output

    # setprop <-name:nox_name | -index:nox_index> -key:<name> -value:<val>
    def setprop(self, idx: int | str, key: str, value: str):
        return self._run(["setprop", f"-index:{idx}", f"-key:{key}", f"-value:{value}"])

    # getprop <-name:nox_name | -index:nox_index> -key:<name>
    def getprop(self, idx: int | str, key: str | None):
        if key is None:
            key = ""
        r = self._run(["getprop", f"-index:{idx}", f"-key:{key}"])
        return r.output

    # installapp <-name:nox_name | -index:nox_index> -filename:<apk_file_name>
    def install_app(self, idx: int | str, apk: str):
        """什么时候安装成功是不知道的"""
        return self._run(["installapp", f"-index:{idx}", f"-filename:{apk}"])

    # uninstallapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def uninstall_app(self, idx: int | str, package: str):
        return self._run(["uninstallapp", f"-index:{idx}", f"-packagename:{package}"])

    # runapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def run_app(self, idx: int | str, package: str):
        self._run(["runapp", f"-index:{idx}", f"-packagename:{package}"])
        time.sleep(3)
        return True

    # killapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def kill_app(self, idx: int | str, package: str):
        self._run(["killapp", f"-index:{idx}", f"-packagename:{package}"])
        time.sleep(3)

    # adb <-name:nox_name | -index:nox_index>  -command:<cmd>
    def adb(self, idx: int | str, cmd: str | list):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self._run(["adb", f"-index:{idx}", f"-command:{cmd}"])

    def adb_shell(self, idx: int | str, cmd: str | list):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb(idx, f"shell {cmd}")

    def adb_deamon(self, idx: int | str, cmd: str | list):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self._run_daemon(["adb", f"-index:{idx}", f"-command:{cmd}"])

    def adb_shell_deamon(self, idx: int | str, cmd: str | list):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb_deamon(idx, f"shell {cmd}")


class NoxPlayerInfo(DeviceInfo):
    pass


class NoxPlayer(Device):
    def __init__(
        self,
        info: DeviceInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        console: NoxConsole | None = None,
    ) -> None:
        super().__init__(info, adb=adb, sleeper=sleeper)

        self.index = info.index
        self.name = info.name
        self.nox_console = NoxConsole(info.console_path) if console is None else console

        self.pid = -1
        """Nox.exe，模拟器界面进程"""
        self.vm_pid = -1
        """NoxVMHandle.exe，负责与 adb 通信的 VM 进程"""

    def is_boot(self) -> bool:
        """判断模拟器是否启动：界面进程和 VM 进程都要在。"""
        pid, vm_pid = self.nox_console.get_pids(self.index)
        self.pid = pid
        self.vm_pid = vm_pid
        return self.pid != -1 and self.vm_pid != -1

    def launch(self):
        self.nox_console.launch_device(self.index)
        while True:
            self._sleep(1)
            if self.is_boot():
                break

    def close(self):
        self.nox_console.quit_device(self.index)
        self._sleep(5)
        self._kill_self()

    def _kill_self(self):
        pid = int(self.pid)
        if pid == -1:
            return

        if psutil.pid_exists(pid):
            p = psutil.Process(pid)
            p.kill()

    def reboot(self):
        self.nox_console.reboot_device(self.index)
        return self.get_status()

    def install_app_by_console(self, apk_path: str) -> CmdResult:
        # NOTE 默认无运行时权限，需要手动授权
        return self.nox_console.install_app(self.index, apk_path)

    def uninstall_app_by_console(self, package_name: str) -> CmdResult:
        return self.nox_console.uninstall_app(self.index, package_name)

    def run_app(self, package: str) -> bool:
        return self.nox_console.run_app(self.index, package)

    def kill_app(self, package: str):
        self.nox_console.kill_app(self.index, package)

    def adb_by_console(self, cmd: list):
        return self.nox_console.adb(self.index, cmd)

    def adb_shell_by_console(self, cmd: list):
        return self.nox_console.adb_shell(self.index, cmd)

    def getprop(self, prop: str | None = None):
        return self.nox_console.getprop(self.index, prop)

    def get_serial(self):
        """ADB 模式，则需要获取模拟器的序列号"""
        ports = set()
        while True:
            net_con = psutil.net_connections()
            for con_info in net_con:
                if con_info.pid == self.vm_pid and con_info.status == "LISTEN":
                    ports.add(con_info.laddr.port)  # type: ignore

            if len(ports) > 0:
                break

            self._adb_wrapper.run_cmd(["devices", "-l"])
            self._sleep(1)

        while True:
            serial = None
            result = self._adb_wrapper.run_cmd(["devices", "-l"])
            for line in result.output.split("\n"):
                if "daemon not running" in line:
                    break

                if "List of devices attached" in line:
                    continue

                parts = line.split()
                serial = parts[0]
                if int(serial.split(":")[-1]) in ports:
                    break
                serial = None

            if serial is None:
                self._sleep(3)
                continue

            self.info.serial = serial
            break
