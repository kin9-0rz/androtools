# 夜神模拟器
import shutil
from time import sleep

import psutil
from loguru import logger

from androtools.core.device import Device, DeviceConsole, DeviceInfo, DeviceStatus


class NoxConsole(DeviceConsole):
    def __init__(self, path=shutil.which("NoxConsole.exe")):
        super().__init__(path)

    def launch_device(self, idx: int | str):
        self._run(["launch", f"-index:{idx}"])
        sleep(3)

    def reboot_device(self, idx: int | str):
        self._run(["reboot", f"-index:{idx}"])
        sleep(3)

    def quit_device(self, idx: int | str):
        self._run(["quit", f"-index:{idx}"])
        sleep(3)

    def quit_all_devices(self):
        """关闭所有的模拟器"""
        self._run(["quitall"])
        sleep(3)

    def list_devices(self) -> str:
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
        r, err = self._run(["list"])
        if err != "":
            logger.error(err)
        return r

    # installapp <-name:nox_name | -index:nox_index> -filename:<apk_file_name>
    def install_app(self, idx: int | str, apk: str):
        r1, r2 = self._run(["installapp", f"-index:{idx}", f"-filename:{apk}"])
        sleep(3)
        return True, r1.strip() + " | " + r2.strip()

    # uninstallapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def uninstall_app(self, idx: int | str, package: str):
        self._run(["uninstallapp", f"-index:{idx}", f"-packagename:{package}"])
        sleep(3)

    # runapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def run_app(self, idx: int | str, package: str):
        self._run(["runapp", f"-index:{idx}", f"-packagename:{package}"])
        sleep(3)
        return True

    # killapp <-name:nox_name | -index:nox_index> -packagename:<apk_package_name>
    def kill_app(self, idx: int | str, package: str):
        self._run(["killapp", f"-index:{idx}", f"-packagename:{package}"])
        sleep(3)


class NoxPlayerInfo(DeviceInfo):
    pass

    # def __init__(
    #     self,
    #     index: str,
    #     serial: str | None,
    #     name: str,
    #     adb_path: str,
    #     console_path: str,
    # ) -> None:
    #     super().__init__(index, serial, name, adb_path, console_path)


class NoxPlayer(Device):
    def __init__(self, info: DeviceInfo, is_reboot: bool = True) -> None:
        self.index = info.index
        self.name = info.name
        self.nox_console = NoxConsole(info.console_path)
        # NOTE Nox模拟器，不一定能关闭，所以，最好是重启。
        super().__init__(info, is_reboot)

    def get_pid(self):
        out = self.nox_console.list_devices()
        pid = -1
        for item in out.split("\n"):
            parts = item.split(",")
            if self.index != parts[0]:
                continue
            pid = int(parts[-1])
            break

        return pid

    def _init_serial(self):
        while True:
            pid = self.get_pid()
            if pid is not None and psutil.pid_exists(pid):
                p = psutil.Process(pid)
                if p.name() == "NoxVMHandle.exe":
                    break

            sleep(3)

        logger.debug(f"NoxPlayer {self.info.name} pid is {pid}")

        if pid is None:
            raise ValueError("NoxPlayer not found")

        ports = set()
        while True:
            net_con = psutil.net_connections()
            for con_info in net_con:
                if con_info.pid == pid and con_info.status == "LISTEN":
                    ports.add(con_info.laddr.port)  # type: ignore

            if len(ports) > 0:
                break

            self._adb_wrapper.run_cmd(["devices", "-l"])
            sleep(1)

        while True:
            serial = None
            out, _ = self._adb_wrapper.run_cmd(["devices", "-l"])
            for line in out.strip().split("\n"):
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
                sleep(3)
                continue

            self.info.serial = serial
            logger.debug(f"NoxPlayer {self.info.name} serial is {self.info.serial}")
            break

    def launch(self):
        self.nox_console.launch_device(self.index)
        while True:
            sleep(1)
            if self.is_boot():
                break

    def close(self):
        self.nox_console.quit_device(self.index)
        sleep(5)
        self._kill_self()

    def _kill_self(self):
        pid = self.get_pid()
        if psutil.pid_exists(pid):
            p = psutil.Process(pid)
            p.kill()

    def reboot(self):
        self.nox_console.reboot_device(self.index)
        while True:
            sleep(1)
            if self.is_boot():
                break

    def is_boot(self) -> bool:
        pid = self.get_pid()
        if pid == -1:
            return False
        return True

    def get_status(self):
        status = DeviceStatus.UNKNOWN
        if self.is_boot():
            status = DeviceStatus.BOOT
        else:
            status = DeviceStatus.STOP

        if status is DeviceStatus.BOOT:
            if self.is_crashed():
                status = DeviceStatus.ERORR

        if status is not DeviceStatus.BOOT:
            return status

        logger.debug("device %s status: %s" % (self.name, status))
        out, err = self.adb_shell(["getprop", "dev.boot_completed"])
        if "error:" in err:
            status = DeviceStatus.ERORR
        if "1" in out:
            status = DeviceStatus.RUN

        out, err = self.adb_shell(["getprop", "sys.boot_completed"])
        if "1" in out:
            status = DeviceStatus.RUN

        out, err = self.adb_shell(["getprop", "init.svc.bootanim"])
        if "stopped" in out:
            status = DeviceStatus.RUN

        return status

    # NOTE 不能使用这个，应用安装后，没有权限。
    # def install_app(self, apk_path: str):
    #     return self.nox_console.install_app(self.index, apk_path)

    def uninstall_app(self, package_name: str):
        self.nox_console.uninstall_app(self.index, package_name)

    def run_app(self, package: str) -> bool:
        return self.nox_console.run_app(self.index, package)

    def kill_app(self, package: str):
        self.nox_console.kill_app(self.index, package)
