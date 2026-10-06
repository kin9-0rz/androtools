# 雷电模拟器
import json
import shutil
import time
from pathlib import Path
from typing import Callable

from androtools import logger
from androtools.android_sdk.platform_tools import AdbRunner
from androtools.core.device import EmulatorConsole, DeviceStatus, EmulatorInfo, Pids
from androtools.core.identity import IDENTITY_CMD, normalize_mac, parse_mac
from androtools.core.session import ConsoleSession


class LDConsole(EmulatorConsole):
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

    def instances(self) -> list[str]:
        """雷电的实例编号就是 list2 的行序，第 0 列。"""
        return [
            line.split(",")[0]
            for line in self.list_devices().splitlines()
            if line.strip()
        ]

    def probe_state(self, idx: int | str) -> DeviceStatus:
        """雷电不给状态字符串，只能看两个进程在不在。"""
        if not self.get_pids(idx).is_running():
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

    def fingerprint(self, idx: int | str) -> str | None:
        """这个实例在雷电自己眼里的身份：它的 MAC。

        读的是雷电写的实例配置 `vms/config/leidian{index}.config` 里的
        `propertySettings.macAddress`。这不是我们的猜测 —— 雷电把这个值注入到
        guest 的 wlan0 上，实测两边一致（配置 00DB48FD6270，
        `ip addr show wlan0` 是 00:db:48:fd:62:70）。

        配置不在就返回 None。不抛异常：雷电改目录布局是我们该降级的情况，
        而调用方有明确的降级方式（不猜，如实报错）。

        注意 `leidian{index}.config` 里的 index 是雷电的实例名，和
        `list2` 的行序一致 —— 实测两台实例分别是 leidian0 / leidian1。
        """
        if not self.bin_path:
            return None
        config = (
            Path(self.bin_path).parent
            / "vms"
            / "config"
            / f"leidian{idx}.config"
        )
        if not config.is_file():
            return None
        try:
            payload = json.loads(config.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            return None

        mac = payload.get("propertySettings.macAddress")
        return normalize_mac(mac) if mac else None


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

    #: 收窄到具体类型，resolve_serial 才拿得到 fingerprint
    console: LDConsole

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

    def resolve_serial(self) -> str:
        """在 adb 现有的设备里认出属于这个 index 的那一台，写进 info.serial。

        为什么必须反查而不能按 index 算端口：雷电的 serial 落在
        `emulator-5554 + 2 * index` 这个号段里，而 **MuMu 占用同一个号段**。
        实测两台雷电 + 两台 MuMu 同时开着：

            emulator-5554  雷电 index 0
            emulator-5556  MuMu index 1     <- 正好是雷电 index 1 该去的位置
            雷电 index 1   在 adb 里根本不出现

        更糟的是雷电官方的 `ldconsole adb --index 1` 会安静地返回 MuMu 那台设备
        的属性。所以按 index 推出的 serial 不会「连不上」，它会**合法地连到别人的
        模拟器**上 —— 对一个要装 APK、跑测试的库来说，这是最坏的失败方式。

        认法是核对 MAC：雷电把每个实例的 MAC 写进自己的实例配置，也注入了 guest
        的 wlan0，两边一定对得上。认不出就报错，不退而求其次去猜。

        Raises:
            RuntimeError: 找不到指纹，或 adb 上没有任何设备与它相符。
        """
        expected = self.console.fingerprint(self.index)
        if expected is None:
            raise RuntimeError(
                f"读不到雷电实例 {self.index} 的 MAC 指纹（找 {self.index} 号实例配置"
                "失败），没有可核对的身份。请在 DeviceInfo.serial 里直接指定 adb serial。"
            )

        candidates = [serial for serial, status, _ in self.adb.get_devices()]
        for serial in candidates:
            try:
                output = self.adb.run_shell_cmd(IDENTITY_CMD, serial).output
            except Exception as e:
                # adb devices 会列出问不动的东西（离线、正在重启）。跳过即可，
                # 整条反查不该因为一台设备不配合就失败。
                logger.debug(f"[{self.name}] 问 {serial} 的 MAC 失败：{e}")
                continue

            if parse_mac(output) == expected:
                self.info.serial = serial
                return serial

        raise RuntimeError(
            f"雷电实例 {self.index} 认不出对应的 adb 设备：adb 上有 "
            f"{candidates or '（无）'}，没有一台的 MAC 是 {expected}。"
            "雷电和 MuMu 共用 emulator-555X 号段，可能被对方的实例占住了；"
            "请在 DeviceInfo.serial 里直接指定。"
        )

    def get_status(self) -> DeviceStatus:
        """问状态之前先把 serial 认出来。

        认得出就用认出的（准），认不出就留给 `_checked_serial` 的歧义检查 ——
        那里的报错本来就带着设备列表，不会比这里差。而 `get_status()` 的契约是
        返回一个状态，不该因为「认不出实例」而抛异常。
        """
        self._auto_resolve_serial()
        return super().get_status()

    def _auto_resolve_serial(self) -> None:
        """serial 没给时按 MAC 认一次。认不出就算了，不硬失败。

        没有指纹可比对时直接跳过 —— 雷电改了目录布局不该让这个类彻底不能用，
        那种情况下 adb 的多设备歧义检查仍然拦得住。
        """
        if self.info.serial is not None or self.console.fingerprint(self.index) is None:
            return
        try:
            self.resolve_serial()
        except RuntimeError as e:
            logger.debug(f"[{self.name}] 自动认 serial 失败：{e}")
