# 雷电模拟器
import shutil
from time import sleep

import psutil
from loguru import logger

from androtools.android_sdk import CMD
from androtools.core.device import Device, DeviceInfo, DeviceStatus


class LDConsole(CMD):
    def __init__(self, path=shutil.which("ldconsole.exe")):
        super().__init__(path)

    def help(self):
        return self._run([])

    def launch_device(self, idx: int | str):
        return self._run(["launch", "--index", str(idx)])

    def reboot_device(self, idx: int | str):
        return self._run(["reboot", "--index", str(idx)])

    def quit_device(self, idx: int | str):
        return self._run(["quit", "--index", str(idx)])

    def quit_all_devices(self):
        return self._run(["quit-all"])

    def list_devices(self):
        """列出所有模拟器信息

        0. 索引
        1. 标题
        2. 顶层窗口句柄
        3. 绑定窗口句柄
        4. 运行状态, 0-停止,1-运行,2-挂起
        5. 进程ID, 不运行则为 -1.
        6. VBox进程PID
        7. 分辨率-宽
        8. 分辨率-高
        9. dpi

        Returns:
            _type_: _description_
        """
        return self._run(["list2"])

    def adb(self, idx, cmd, encoding: str | None = None):
        assert isinstance(cmd, str)
        cmd = ["adb", "--index", str(idx), "--command", cmd]
        logger.debug(f"LDConsole - {' '.join(cmd)}")

        return self._run(cmd, encoding=encoding)

    def adb_daemon(self, idx, cmd, encoding: str | None = None):
        assert isinstance(cmd, str)
        cmd = ["adb", "--index", str(idx), "--command", cmd]
        logger.debug(f"LDConsole - {' '.join(cmd)}")
        self._run_daemon(cmd)

    def adb_shell(self, idx, cmd: str | list, encoding: str | None = None):
        logger.debug(f"LDConsole - {idx}, {cmd}")
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb(idx, f"shell {cmd}", encoding=encoding)

    def adb_shell_daemon(self, idx, cmd: str | list, encoding: str | None = None):
        logger.debug(f"LDConsole - {idx}, {cmd}")
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        self.adb_daemon(idx, f"shell {cmd}", encoding=encoding)


class LDPlayerInfo(DeviceInfo):
    def __init__(self, index: str, name: str, path: str) -> None:
        self.index = index
        self.name = name
        self.console_path = path

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, LDPlayerInfo):
            return False

        return self.index == __value.index and self.console_path == __value.console_path


def find_adb():
    for process in psutil.process_iter():
        if process.name() == "adb.exe":
            return True
    return False


class LDPlayer(Device):
    """
    adb 有两种选择
    1. 使用 adb
    2. 使用 console的adb命令。
    """

    def __init__(self, info: LDPlayerInfo) -> None:
        self.index = info.index
        self.name = info.name
        self.ldconsole = LDConsole(info.console_path)
        super().__init__(info)

    def launch(self):
        self.ldconsole.launch_device(self.info.index)
        if not find_adb():
            self.adb_daemon("start-server")
            sleep(3)

        while True:
            r = self.get_status()
            if r is DeviceStatus.RUN:
                break
            if r is DeviceStatus.ADB_ERR:
                self.adb("kill-server")
                sleep(3)
                self.adb_daemon("start-server")
                sleep(3)
            sleep(1)
        sleep(10)

    def close(self):
        self.ldconsole.quit_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.STOP:
                break
            sleep(1)

    def reboot(self):
        self.ldconsole.reboot_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.RUN:
                break
            if r is DeviceStatus.ADB_ERR:
                self.adb("kill-server")
                sleep(3)
                self.adb("start-server")
                sleep(3)
            sleep(1)
        sleep(10)

    def get_status(self):
        status = DeviceStatus.UNKNOWN
        out, _ = self.ldconsole.list_devices()
        for line in out.strip().split("\n"):
            if self.name not in line:
                continue
            parts = line.split(",")
            status = DeviceStatus.get(parts[4])
            break

        if status is DeviceStatus.RUN:
            out, err = self.adb_shell("ps")
            if "offline" in out or "not found" in out:
                status = DeviceStatus.ADB_ERR

            # adb.exe: device offline
            elif self.is_crashed():
                status = DeviceStatus.ERORR

        return status

    def adb(self, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        logger.debug(f"LDPlayer - {cmd}")
        return self.ldconsole.adb(self.index, cmd, encoding=encoding)

    def adb_shell(self, cmd: str | list, encoding: str | None = None):
        logger.debug(f"LDPlayer - {cmd}")
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        if isinstance(cmd, str):
            cmd = f"shell {cmd}"

        return self.adb(cmd, encoding=encoding)

    def adb_daemon(self, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        logger.debug(f"LDPlayer - {cmd}")
        self.ldconsole.adb_daemon(self.index, cmd, encoding=encoding)

    def adb_shell_daemon(self, cmd: str | list, encoding: str | None = None):
        logger.debug(f"LDPlayer - {cmd}")
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        if isinstance(cmd, str):
            cmd = f"shell {cmd}"

        self.adb_daemon(cmd, encoding=encoding)
