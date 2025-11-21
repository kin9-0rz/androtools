import shutil
from enum import Enum
from time import sleep

import psutil

from androtools.cmd import CMD
from androtools import logger


class DeviceType(Enum):
    Default = 0
    Serial = 1
    TransportID = 2  #  Android 8.0 (API level 26) adb version 1.0.41


class DeviceOfflineError(Exception):
    pass


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

    def get_devices(self, max_tries=10):
        devices = []

        counter = 0
        while True:
            counter += 1
            if counter == max_tries:
                return devices

            self.run_cmd(["devices", "-l"])
            self.run_cmd(["devices", "-l"])

            result = self.run_cmd(["devices", "-l"])
            output = result.output.strip()
            if output == "List of devices attached":
                sleep(0.5)
                continue

            if "127.0.0.1:" not in output:
                break
            self.restart_server()
            sleep(5)

        lines = output.strip().splitlines()
        if len(lines) <= 1:
            return devices

        for line in lines[1:]:
            arr = line.split()
            name = arr[0]
            status = arr[1]
            tid = arr[-1].split(":")[-1]
            devices.append((name, status, tid))

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
