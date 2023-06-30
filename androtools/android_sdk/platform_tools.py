import logging
import shutil
import subprocess
import sys
from enum import Enum
from time import sleep

from androtools.android_sdk import CMD


class DeviceType(Enum):
    Default = 0
    Serial = 1
    TransportID = 2  #  Android 8.0 (API level 26) adb version 1.0.41


class ADB:
    def __init__(self):
        self.adb_path = shutil.which("adb")
        logging.debug(f"adb path: {self.adb_path}")
        self.cmd_prefix = [self.adb_path]

    def set_target_device(self, device_name, device_type: DeviceType):
        self.cmd_prefix = [self.adb_path]
        match (device_type):
            case DeviceType.Default:
                self.target_device = None
            case DeviceType.Serial:
                self.cmd_prefix.append("-s")
                self.cmd_prefix.append(device_name)
            case DeviceType.TransportID:
                self.cmd_prefix.append("-t")
                self.cmd_prefix.append(device_name)
            case _:
                raise ValueError(f"unknown device type: {device_type}")

    def list_devices(self):
        output, _ = self.run_cmd(["devices", "-l"])
        devices = []
        transport_ids = []

        for line in output[1:]:
            arr = line.split()
            devices.append(arr[0])
            transport_ids.append(arr[-1].split(":"))

        return devices, transport_ids

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

    def restart_server(self):
        self.kill_server()
        self.start_server()

    def _build_cmds(self, cmd: str | list, is_shell: bool = False):
        if isinstance(cmd, str):
            cmd = [cmd]

        full_cmd = self.cmd_prefix + cmd
        if is_shell:
            full_cmd = self.cmd_prefix + ["shell"] + cmd

        return " ".join(full_cmd) if sys.platform.startswith("win") else full_cmd

    def run_cmd(self, cmd: str | list, is_shell: bool = False):
        cmd_list = self._build_cmds(cmd, is_shell)
        logging.debug(cmd_list)
        try:
            adb_proc = subprocess.Popen(
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            output, error = adb_proc.communicate()
            output = output.decode("utf-8")
            error = error.decode("utf-8")

            if adb_proc.returncode == 1:
                logging.error(cmd_list)
                return output, error

            if len(output) == 0:
                output = None
            else:
                output = [x.strip() for x in output.split("\n") if len(x.strip()) > 0]

        except Exception as err:
            logging.error(cmd_list)
            logging.error(err)
            raise err

        return output, error

    def run_shell_cmd(self, cmd: str | list):
        return self.run_cmd(cmd, is_shell=True)


class FastBoot(CMD):
    def __init__(self, path=shutil.which("fastboot")) -> None:
        super().__init__(path)

    def help(self):
        print(self._run(f"{self.bin_path} -h")[0])
