# 雷电模拟器
import shutil
from time import sleep

from loguru import logger

from androtools.android_sdk import CMD
from androtools.core.emu import Emu, EmuInfo, EmuStatus, WorkStatus


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
        print("adb", idx, cmd)
        assert isinstance(cmd, str)
        return self._run(
            ["adb", "--index", str(idx), "--command", cmd], encoding=encoding
        )

    def adb_shell(self, idx, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb(idx, f"shell {cmd}", encoding=encoding)


class LDPlayerInfo(EmuInfo):
    def __init__(self, index: str, name: str, path: str) -> None:
        self.index = index
        self.name = name
        self.path = path

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, LDPlayerInfo):
            return False

        return self.index == __value.index and self.path == __value.path


class LDPlayer(Emu):
    def __init__(self, info: LDPlayerInfo) -> None:
        self.index = info.index
        self.name = info.name
        self.ldconsole = LDConsole(info.path)
        super().__init__(info)

    def launch(self):
        self.ldconsole.launch_device(self.index)
        while True:
            r = self.get_status()
            if r is EmuStatus.RUN:
                break
            sleep(1)
        sleep(10)

    def close(self):
        self.ldconsole.quit_device(self.index)
        while True:
            r = self.get_status()
            if r is EmuStatus.STOP:
                break
            sleep(1)

    def reboot(self):
        self.ldconsole.reboot_device(self.index)
        while True:
            r = self.get_status()
            if r is EmuStatus.RUN:
                break
            sleep(1)
        sleep(10)

    def get_status(self):
        status = EmuStatus.UNKNOWN
        out, _ = self.ldconsole.list_devices()
        for line in out.strip().split("\n"):
            if self.name not in line:
                continue
            parts = line.split(",")
            status = EmuStatus.get(parts[4])
            break

        if status is EmuStatus.RUN:
            if self.is_crashed():
                status = EmuStatus.ERORR

        return status

    def adb(self, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.ldconsole.adb(self.index, cmd, encoding=encoding)

    def adb_shell(self, cmd: str | list, encoding: str | None = None):
        return self.ldconsole.adb_shell(self.index, cmd, encoding=encoding)


class LDPlayerManger:
    """
    只能管理 Android 同版的模拟器，不同版本，无法执行 adb。
    1. 根据已知设备初始化。
    2. 增加设备。
    3. 删除设备。
    """

    # NOTE 雷电模拟器多开的时候，只能开同一个版本，多个版本在执行 adb 命令时，会卡死。

    def __init__(self, infos: list[LDPlayerInfo]):
        self._infos = infos
        self._devices: dict[LDPlayer, WorkStatus] = {}
        self._init()

    def _init(self):
        self._devices.clear()
        for info in self._infos:
            ldp = LDPlayer(info)
            self._devices[ldp] = WorkStatus.Free
            if ldp.get_status() is not EmuStatus.RUN:
                ldp.launch()

    def add(self, info: LDPlayerInfo):
        self._infos.append(info)
        ldp = LDPlayer(info)
        if ldp.get_status() is not EmuStatus.RUN:
            ldp.launch()
        self._devices[ldp] = WorkStatus.Free

    def remove(self, info: LDPlayerInfo):
        self._infos.remove(info)
        for device in self._devices:
            if device.info == info:
                device.close()
                self._devices.pop(device)
                break

    def get_total(self) -> int:
        return len(self._devices)

    def get_free_device(self) -> LDPlayer | None:
        for device in self._devices:
            if self._devices[device] == WorkStatus.Free:
                self._devices[device] = WorkStatus.Busy
                logger.debug(f"free device: {device}")
                return device
        return None

    def free_busy_device(self, device: LDPlayer):
        if device not in self._devices:
            return
        self._devices[device] = WorkStatus.Free
