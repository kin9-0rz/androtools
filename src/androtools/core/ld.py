# 雷电模拟器
import shutil
import time
from typing import Callable

from androtools import logger
from androtools.android_sdk.platform_tools import AdbRunner
from androtools.core.device import DeviceConsole, DeviceStatus, EmulatorInfo, Pids
from androtools.core.session import ConsoleSession


class LDConsole(DeviceConsole):
    """使用 ldconsole.exe 对模拟器进行管理"""

    def __init__(self, path=shutil.which("ldconsole.exe")):
        super().__init__(path)

    def list_devices(self) -> str:
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
        """
        return self._run(["list2"]).output

    def get_pids(self, idx: int | str) -> Pids:
        """按 list2 的列序取 PID：第 5 列是界面进程，第 6 列是 VBox 进程。"""
        lines = self.list_devices().splitlines()
        parts = lines[int(idx)].split(",")

        return Pids(int(parts[5]), int(parts[6]))

    def probe_state(self, idx: int | str) -> DeviceStatus:
        """雷电不给状态字符串，只能看两个进程在不在。"""
        pids = self.get_pids(idx)
        if pids.ui == -1 or pids.vm == -1:
            return DeviceStatus.STOP
        return DeviceStatus.BOOT

    def launch_device(self, idx: int | str):
        return self._run(["launch", "--index", str(idx)])

    def reboot_device(self, idx: int | str):
        return self._run(["reboot", "--index", str(idx)])

    def quit_device(self, idx: int | str):
        return self._run(["quit", "--index", str(idx)])

    def is_running(self, idx: int | str) -> bool:
        return self._run(["isrunning", "--index", str(idx)]).output == "running"

    def getprop(self, idx: int | str, prop: str | None = None) -> str:
        if prop:
            return self._run(["getprop", "--index", str(idx), "--key", prop]).output
        return self._run(["getprop", "--index", str(idx)]).output

    # setprop <--name mnq_name | --index mnq_idx> --key <name> --value <val>
    def setprop(self, idx: int | str, prop: str, val: str):
        return self._run(
            ["setprop", "--index", str(idx), "--key", prop, "--value", val]
        )

    # installapp <--name mnq_name | --index mnq_idx> --filename <apk_file_name>
    def install_app(self, idx: int | str, apk_path: str):
        return self._run(["installapp", "--index", str(idx), "--filename", apk_path])

    # uninstallapp <--name mnq_name | --index mnq_idx> --packagename <apk_package_name>
    def uninstall_app(self, idx: int | str, package_name: str):
        return self._run(
            ["uninstallapp", "--index", str(idx), "--packagename", package_name]
        )

    # runapp <--name mnq_name | --index mnq_idx> --packagename <apk_package_name>
    def run_app(self, idx: int | str, package: str) -> bool:
        self._run(["runapp", "--index", str(idx), "--packagename", package])
        return True

    # killapp <--name mnq_name | --index mnq_idx> --packagename <apk_package_name>
    def kill_app(self, idx: int | str, package: str) -> None:
        self._run(["killapp", "--index", str(idx), "--packagename", package])

    # locate <--name mnq_name | --index mnq_idx> --LLI <Lng,Lat>
    def locate(self, idx: int | str, lng: float, lat: float):
        return self._run(["locate", "--index", str(idx), "--LLI", f"{lng},{lat}"])

    def adb(self, idx, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self._run(["adb", "--index", str(idx), "--command", cmd], encoding=encoding)

    def adb_shell(self, idx, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb(idx, f"shell {cmd}", encoding=encoding)


class LDPlayer(ConsoleSession):
    """雷电模拟器"""

    def __init__(
        self,
        info: EmulatorInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        console: LDConsole | None = None,
    ) -> None:
        super().__init__(
            info,
            LDConsole(info.console_path) if console is None else console,
            adb=adb,
            sleeper=sleeper,
        )

    def is_crashed(self) -> bool:
        """
        判断模拟器是否没响应，如果没响应，则定义为模拟器崩溃
        需要判断设备崩溃吗？
        """
        # NOTE - 注意：每个模拟器的情况不一样！

        # 启动 com.android.settings
        self.shell.adb_shell(["am", "start", "com.android.settings"])
        self._sleep(3)
        # dumpsys window windows | grep mCurrentFocus
        result = self.shell.adb_shell(
            ["dumpsys", "window", "windows", "|", "grep", "mCurrentFocus"]
        )

        if result.output_contain("com.android.settings"):
            logger.warning(f"[{self.name}] 无法启动设置，杀死系统界面，重新启动设置。")
            self.shell.kill_app("com.android.launcher3")
            self.shell.adb_shell(["am", "start", "com.android.settings"])
            out = self.shell.adb_shell(
                ["dumpsys", "window", "windows", "|", "grep", "mCurrentFocus"]
            ).output
            if "com.android.settings" not in out:
                logger.warning(f"[{self.name}] 无法启动设置，可能需要重启模拟器。")
                return True

        self.shell.home()
        result = self.shell.adb_shell(
            ["dumpsys", "window", "windows", "|", "grep", "mCurrentFocus"]
        )
        if result.output_contain("com.android.launcher3"):
            logger.warning(f"[{self.name}] 回到桌面失败，请检查模拟器是否正常启动。")
            return True
        return False

    def launch(self) -> None:
        if self.is_boot():
            return

        while True:
            self.console.launch_device(self.index)
            self._sleep(10)
            if self.is_boot():
                break

            self.close()
            self._sleep(10)

    def close(self) -> None:
        self.console.quit_device(self.index)
        self._sleep(5)