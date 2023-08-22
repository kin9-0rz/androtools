import logging
import shutil
from enum import Enum
from time import sleep

import psutil

from androtools.android_sdk import CMD


class DeviceType(Enum):
    Default = 0
    Serial = 1
    TransportID = 2  #  Android 8.0 (API level 26) adb version 1.0.41


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

    def __init__(self, path: str = None) -> None:
        if path is None:
            path = shutil.which("adb")
        super().__init__(path)
        self._cmd_target_device = []

    def run_cmd(self, cmd: list):
        assert isinstance(cmd, list)
        logging.debug("run_cmd: %s", cmd)
        return self._run(cmd)

    def help(self):
        output, _ = self.run_cmd([])
        print(output)

    def _build_cmds(self, cmd: list):
        assert isinstance(cmd, list)
        return [self.bin_path] + self._cmd_target_device + cmd

    def set_target_device(self, device_name, device_type: DeviceType):
        assert isinstance(device_type, DeviceType)
        match (device_type):
            case DeviceType.Serial:
                self._cmd_target_device.append("-s")
                self._cmd_target_device.append(device_name)
            case DeviceType.TransportID:
                self._cmd_target_device.append("-t")
                self._cmd_target_device.append(device_name)

    def run_shell_cmd(self, cmd: list):
        assert isinstance(cmd, list)
        return self.run_cmd(self._cmd_target_device + ["shell"] + cmd)

    def get_devices(self):
        while True:
            self.run_cmd(["devices", "-l"])
            sleep(1)
            self.run_cmd(["devices", "-l"])
            sleep(1)

            output, _ = self.run_cmd(["devices", "-l"])
            output = output.strip()
            if output == "List of devices attached":
                sleep(3)
                continue

            if "127.0.0.1:" not in output:
                break
            self.restart_server()
            sleep(5)

        lines = output.strip().splitlines()
        if len(lines) <= 1:
            return None, None

        devices = []
        for line in lines[1:]:
            arr = line.split()
            name = arr[0]
            status = arr[1]
            tid = arr[-1].split(":")[-1]
            devices.append((name, status, tid))

        return devices

    def connect(self, host: str, port: int):
        output, _ = self.run_cmd(["connect", f"{host}:{port}"])
        return "Connection refused" not in output

    def kill_server(self):
        self.run_cmd(["kill-server"])

    def start_server(self):
        output, error = self.run_cmd(["start-server"])
        if "daemon started successfully" in error:
            logging.debug("adb-server start success")
        else:
            logging.error(output)
            logging.error(error)
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
        result, _ = self._run([self.bin_path, "-h"])
        print(result)

    def devices(self, flag=False):
        """List devices in bootloader"""
        _cmd = [self.bin_path, "devices"]
        if flag:
            _cmd.append("-l")
        result, _ = self._run(_cmd)
        print(result)

    def getvar(self, key="all"):
        """获取设备和分区信息"""
        _cmd = [self.bin_path, "getvar", key]
        result, _ = self._run(_cmd)
        print(result)

    def reboot(self, bootloader=False):
        _cmd = [self.bin_path, "reboot"]
        if bootloader:
            _cmd.append("bootloader")
        result, _ = self._run(_cmd)
        print(result)

    def boot(self):
        pass

    # locking/unlocking
    # sub command
    def lock(self):
        _cmd = [
            self.bin_path,
            "flashing",
        ]
        result, _ = self._run(_cmd)
        print(result)

    def unlock(self):
        pass

    # Flashing ...
    def update(self, zip_path):
        """Flash all partitions from an update.zip package."""
        _cmd = [self.bin_path, "update", zip_path]
        result, _ = self._run(_cmd)
        print(result)

    def flash(self, partition, filename):
        """
        Flash given partition, using the image from
        $ANDROID_PRODUCT_OUT if no filename is given.
        """
        _cmd = [self.bin_path, "flash", partition, filename]
        result, _ = self._run(_cmd)
        print(result)

    def flashall(self):
        """
        Flash all partitions from $ANDROID_PRODUCT_OUT.
        On A/B devices, flashed slot is set as active.
        Secondary images may be flashed to inactive slot.
        """
        _cmd = [self.bin_path, "flashall"]
        result, _ = self._run(_cmd)
        print(result)
