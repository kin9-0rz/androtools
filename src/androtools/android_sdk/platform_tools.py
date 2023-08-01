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
        self.run_cmd(["devices", "-l"])
        sleep(1)
        self.run_cmd(["devices", "-l"])
        sleep(1)
        output, _ = self.run_cmd(["devices", "-l"])
        devices = []
        transport_ids = []

        lines = output.strip().splitlines()
        if len(lines) <= 1:
            return None, None

        for line in lines[1:]:
            arr = line.split()
            devices.append(arr[0])
            transport_ids.append(arr[-1].split(":"))

        return devices, transport_ids

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

    def restart_server(self, force=False):
        if not force:
            for proc in psutil.process_iter():
                if 'terminated' in str(proc):
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
