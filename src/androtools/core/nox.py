# 夜神模拟器
import shutil
from time import sleep

from androtools.android_sdk import CMD
from androtools.core.device import Device, DeviceInfo, DeviceStatus


class NoxADB(CMD):
    def __init__(self, path=shutil.which("nox_adb.exe")):
        super().__init__(path)

    def help(self):
        return self._run([])

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
        return self._run(["list"])

    def adb(self, idx, cmd, encoding: str | None = None):
        assert isinstance(cmd, str)
        return self._run(["adb", f"--index{idx}", f"-command:{cmd}"], encoding=encoding)

    def adb_shell(self, idx, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb(idx, f"shell {cmd}", encoding=encoding)


class NoxPlayerInfo(DeviceInfo):
    def __init__(self, index: str, name: str, path: str) -> None:
        self.index = index
        self.name = name
        self.path = path

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, NoxPlayerInfo):
            return False

        return self.index == __value.index and self.path == __value.path


class NoxPlayer(Device):
    def __init__(self, info: NoxPlayerInfo) -> None:
        self.index = info.index
        self.name = info.name
        self.nox_adb = NoxADB(info.path)
        super().__init__(info)

    def launch(self):
        self.nox_adb.launch_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.RUN:
                break
            sleep(1)
        sleep(10)

    def close(self):
        self.nox_adb.quit_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.STOP:
                break
            sleep(1)

    def reboot(self):
        self.nox_adb.reboot_device(self.index)
        while True:
            r = self.get_status()
            if r is DeviceStatus.RUN:
                break
            sleep(1)
        sleep(10)

    def get_status(self):
        status = DeviceStatus.UNKNOWN
        out, _ = self.nox_adb.list_devices()
        for line in out.strip().split("\n"):
            if self.name not in line:
                continue
            parts = line.split(",")
            status = DeviceStatus.get(parts[4])
            break

        if status is DeviceStatus.RUN:
            if self.is_crashed():
                status = DeviceStatus.ERORR

        return status

    def adb(self, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.nox_adb.adb(self.index, cmd, encoding=encoding)

    def adb_shell(self, cmd: str | list, encoding: str | None = None):
        return self.nox_adb.adb_shell(self.index, cmd, encoding=encoding)
