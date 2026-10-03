# 夜神模拟器
import shutil
import time
from typing import Callable

import psutil

from androtools.android_sdk.platform_tools import AdbRunner
from androtools.core.device import DeviceConsole, DeviceInfo
from androtools.core.session import ConsoleSession


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
        2. 标题
        3. 工具栏窗口句柄
        4. Nox.exe 模拟器进程
        5. NoxVMHandle.exe；这个进程和adb连接，它最先启动
        """
        return self._run(["list"]).output

    # setprop <-name:nox_name | -index:nox_index> -key:<name> -value:<val>
    def setprop(self, idx: int | str, key: str, value: str):
        return self._run(["setprop", f"-index:{idx}", f"-key:{key}", f"-value:{value}"])

    # getprop <-name:nox_name | -index:nox_index> -key:<name>
    def getprop(self, idx: int | str, prop: str | None) -> str:
        return self._run(["getprop", f"-index:{idx}", f"-key:{prop or ''}"]).output

    # installapp <-name:nox_name | -index:nox_index> -filename:<apk_file_name>
    def install_app(self, idx: int | str, apk: str):
        """什么时候安装成功是不知道的"""
        return self._run(["installapp", f"-index:{idx}", f"-filename:{apk}"])

    # uninstallapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def uninstall_app(self, idx: int | str, package: str):
        return self._run(["uninstallapp", f"-index:{idx}", f"-packagename:{package}"])

    # runapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def run_app(self, idx: int | str, package: str) -> bool:
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


class NoxPlayer(ConsoleSession):
    def __init__(
        self,
        info: DeviceInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        console: NoxConsole | None = None,
    ) -> None:
        super().__init__(
            info,
            NoxConsole(info.console_path) if console is None else console,
            adb=adb,
            sleeper=sleeper,
        )
        self.pid = -1
        """Nox.exe，界面进程 PID"""
        self.vm_pid = -1
        """NoxVMHandle.exe，负责与 adb 通信的 VM 进程 PID"""

    def is_boot(self) -> bool:
        """判断模拟器是否启动：界面进程和 VM 进程都要在。"""
        pid, vm_pid = self.console.get_pids(self.index)
        self.pid = pid
        self.vm_pid = vm_pid
        return self.pid != -1 and self.vm_pid != -1

    def launch(self) -> None:
        self.console.launch_device(self.index)
        while True:
            self._sleep(1)
            if self.is_boot():
                break

    def close(self) -> None:
        self.console.quit_device(self.index)
        self._sleep(5)
        self._kill_self()

    def recover(self):
        self.console.reboot_device(self.index)
        return self.get_status()

    def _kill_self(self) -> None:
        if self.pid == -1:
            return

        if psutil.pid_exists(self.pid):
            psutil.Process(self.pid).kill()

    def get_serial(self) -> None:
        """夜神启动后端口会变，序列号要在启动过程中反查出来。"""
        ports = set()
        while True:
            net_con = psutil.net_connections()
            for con_info in net_con:
                if con_info.pid == self.vm_pid and con_info.status == "LISTEN":
                    ports.add(con_info.laddr.port)  # type: ignore[union-attr]

            if len(ports) > 0:
                break

            self.shell.adb(["devices", "-l"])
            self._sleep(1)

        while True:
            serial = None
            result = self.shell.adb(["devices", "-l"])
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