import shutil
from enum import Enum
from time import sleep
from typing import Protocol

import psutil

from androtools.cmd import CMD
from androtools.cmd.result import CmdResult
from androtools import logger


class DeviceType(Enum):
    Default = 0
    Serial = 1
    TransportID = 2  #  Android 8.0 (API level 26) adb version 1.0.41


class DeviceOfflineError(Exception):
    pass


class AmbiguousDeviceError(Exception):
    """没有指定 serial，而 adb 那边有多台设备在线 —— adb 不知道该操作哪一台。"""


class AdbRunner(Protocol):
    """执行 adb 命令的 interface，Device 的生命周期逻辑全走这个 seam。

    seam 放在 Device 层而不是 CMD 层：CMD._run 是所有外部命令（aapt2、
    avdmanager、fastboot、厂商控制台）的唯一执行点，把 seam 开在那里要动 7 个
    子类，而其中多数无人使用、无法验证。开在这里则 ADB 天然满足 interface，
    真实实现与 FakeADB 两个 adapter 就足以让 seam 成立。

    serial 保留在 interface 上是有意的：它对绝大多数调用是同一个值，但夜神
    模拟器在启动过程中会改写 DeviceInfo.serial，测试和调用方都需要看到
    「当前用的是哪个 serial」。

    get_devices 也在 interface 上：serial 为空时需要靠它判断「只有一台设备」
    才敢不加 -s，否则 adb 会直接拒绝。
    """

    def run_cmd(
        self, cmd: list[str], serial: str | None = None, timeout: int = 30
    ) -> CmdResult: ...

    def run_shell_cmd(
        self, cmd: list[str], serial: str | None = None, timeout: int = 30
    ) -> CmdResult: ...

    def run_shell_cmd_daemon(
        self, cmd: list[str], serial: str | None = None
    ) -> None: ...

    def get_devices(self) -> list[tuple[str, str, str]]:
        """在线目标列表，每项是 (serial, status, transport id)。"""


class ADB(CMD):
    """仅仅执行命令，仅仅执行adb命令，不执行与设备无关的命令，比如:adb shell
    请使用 Device。
    Args:
        CMD (_type_): _description_

    Raises:
        ValueError: _description_

    Returns:
        _type_: _description_
    """

    def __init__(self, path: str | None = None) -> None:
        if path is None:
            path = shutil.which("adb")

        if path is None:
            raise ValueError("adb not found")

        super().__init__(path)

    @staticmethod
    def build_su_cmd(cmd: list[str]):
        cmd = ["su", "0"] + cmd
        return cmd

    def run_cmd(self, cmd: list[str], serial: str | None = None, timeout: int = 30):
        """执行 adb 命令"""
        assert isinstance(cmd, list)
        if serial is not None:
            cmd = ["-s", serial] + cmd
        return self._run(cmd, timeout=timeout)

    def run_shell_cmd(
        self, cmd: list[str], serial: str | None = None, timeout: int = 30
    ):
        """执行 adb shell 命令"""
        assert isinstance(cmd, list)
        cmd = ["shell"] + cmd
        result = self.run_cmd(cmd, serial, timeout)
        if result.contain("offline"):
            raise DeviceOfflineError("设备已断开")
        return result

    def run_cmd_daemon(self, cmd: list[str]):
        self._run_daemon(cmd)

    def run_shell_cmd_daemon(self, cmd: list[str], serial: str | None = None):
        """执行 adb shell 命令"""
        assert isinstance(cmd, list)
        cmd = ["shell"] + cmd
        if serial is not None:
            cmd = ["-s", serial] + cmd
        self._run_daemon(cmd)

    def help(self):
        result = self.run_cmd([])
        return result.output

    def get_devices(self) -> list[tuple[str, str, str]]:
        """列出 adb 当前能看到的每一个目标。

        纯查询：问一次，解析，返回。重试和重连 adb server 不归它管 —— 那是调用方
        在知道「模拟器可能还没连上」时才该做的事。

        Returns:
            每个在线目标是 (serial, status, transport id)，顺序同 adb 输出。
        """
        output = self.run_cmd(["devices", "-l"]).output.strip()
        lines = output.splitlines()
        if len(lines) <= 1:
            return []

        devices = []
        for line in lines[1:]:
            # adb 自己打的提示行（如 daemon 启动信息）以 * 开头，不是设备
            if line.startswith("*"):
                continue

            arr = line.split()
            if len(arr) < 2:
                continue

            devices.append((arr[0], arr[1], arr[-1].split(":")[-1]))

        return devices

    def connect(self, host: str, port: int):
        result = self.run_cmd(["connect", f"{host}:{port}"])
        return result.contain("Connection refused")
        # return "Connection refused" not in output

    def kill_server(self):
        self.run_cmd(["kill-server"])

    def start_server(self):
        result = self.run_cmd(["start-server"])
        if not result.contain("daemon started successfully"):
            logger.error("=" * 80)
            logger.error("output:\n" + result.output)
            logger.error("error:\n" + result.error, stack_info=True)
            logger.error("=" * 80)
            sleep(1)
            self.kill_server()
            self.start_server()

        sleep(3)  # 等待3秒，等待模拟器启动

    def restart_server(self, force=True):
        if not force:
            for proc in psutil.process_iter():
                if "terminated" in str(proc):
                    continue
                name = proc.name()
                if name in {"adb", "adb.exe"}:
                    return
        self.kill_server()
        self.start_server()


class FastBoot(CMD):
    def __init__(self, path=shutil.which("fastboot")) -> None:
        super().__init__(path)

    def help(self):
        # NOTE -h 命令不支持 shell
        assert self.bin_path is not None
        result = self._run([self.bin_path, "-h"])
        print(result.output)

    def devices(self, flag=False):
        """List devices in bootloader"""
        _cmd = [self.bin_path, "devices"]
        if flag:
            _cmd.append("-l")
        result = self._run(_cmd)
        logger.debug(result)

    def getvar(self, key="all"):
        """获取设备和分区信息"""
        _cmd = [self.bin_path, "getvar", key]
        result = self._run(_cmd)
        logger.debug(result)

    def reboot(self, bootloader=False):
        _cmd = [self.bin_path, "reboot"]
        if bootloader:
            _cmd.append("bootloader")
        result = self._run(_cmd)
        logger.debug(result)

    def boot(self):
        pass

    # locking/unlocking
    # sub command
    def lock(self):
        _cmd = [
            self.bin_path,
            "flashing",
        ]
        result = self._run(_cmd)
        logger.debug(result)

    def unlock(self):
        pass

    # Flashing ...
    def update(self, zip_path):
        """Flash all partitions from an update.zip package."""
        _cmd = [self.bin_path, "update", zip_path]
        result = self._run(_cmd)
        logger.debug(result)

    def flash(self, partition, filename):
        """
        Flash given partition, using the image from
        $ANDROID_PRODUCT_OUT if no filename is given.
        """
        _cmd = [self.bin_path, "flash", partition, filename]
        result = self._run(_cmd)
        logger.debug(result)

    def flashall(self):
        """
        Flash all partitions from $ANDROID_PRODUCT_OUT.
        On A/B devices, flashed slot is set as active.
        Secondary images may be flashed to inactive slot.
        """
        _cmd = [self.bin_path, "flashall"]
        result = self._run(_cmd)
        logger.debug(result)
