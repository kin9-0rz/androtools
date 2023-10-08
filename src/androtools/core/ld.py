# 雷电模拟器
import shutil
from time import sleep

from loguru import logger

from androtools.android_sdk import CMD
from androtools.core.constants import DeviceState, KeyEvent


class LDConsole(CMD):
    def __init__(self, path=None):
        if path is None:
            path = shutil.which("ldconsole.exe")
        super().__init__(path)

    def launch_device(self, idx: int):
        return self._run(["launch", "--index", str(idx)])

    def reboot_device(self, idx: int):
        return self._run(["reboot", "--index", str(idx)])

    def quit_device(self, idx: int):
        return self._run(["quit", "--index", str(idx)])

    def quit_all_devices(self):
        return self._run(["quit-all"])

    def list_devices(self):
        """列出所有模拟器信息

        索引，标题，顶层窗口句柄，绑定窗口句柄，运行状态，进程ID，VBox进程PID，分辨率-宽，分辨率-高，dpi。

        - 运行状态：0-停止,1-运行,2-挂起
        - 进程ID：不运行则为-1.

        Returns:
            _type_: _description_
        """
        return self._run(["list2"])

    def install_app(self, idx, path):
        return self._run(["installapp", "--index", str(idx), path])

    def uninstall_app(self, idx, package):
        return self._run(["uninstall", "--index", str(idx), package])

    def run_app(self, idx, package):
        return self._run(["runapp", "--index", str(idx), "--packagename", package])

    def kill_app(self, idx, package):
        return self._run(["killapp", "--index", str(idx), "--packagename", package])

    def pull(self, idx, remote, local):
        return self._run(
            ["pull", "--index", str(idx), "--remote", remote, "--local", local]
        )

    def push(self, idx, local, remote):
        return self._run(
            ["push", "--index", str(idx), "--local", local, "--remote", remote]
        )

    def adb(self, idx, cmd):
        return self._run(["adb", "--index", str(idx), "--command", '"', cmd, '"'])

    def adb_shell(self, idx, cmd: str | list):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb(idx, f"shell {cmd}")


class LDPlayerInfo:
    index: str
    name: str
    ldconsole: LDConsole

    def __init__(self, index: int, name: str, ldconsole_path: str = None) -> None:
        self.index = index
        self.name = name
        self.ldconsole = LDConsole(ldconsole_path)


class LDPlayer:
    # def __init__(self, index: int, name: str, ldconsole_path: str = None) -> None:
    def __init__(self, info: LDPlayerInfo) -> None:
        self.index = info.index
        self.name = info.name
        self.ldconsole = info.ldconsole

    def launch(self):
        self.ldconsole.launch_device(self.index)

    def close(self):
        self.ldconsole.quit_device(self.index)

    def reboot(self):
        self.ldconsole.reboot_device(self.index)

    def get_status(self):
        return self.ldconsole.list_devices()

    def install_appp(self, path):
        self.ldconsole.install_app(self.index, path)

    def uninstall_appp(self, package):
        self.ldconsole.uninstall_app(self.index, package)

    def run_appp(self, package):
        self.ldconsole.run_app(self.index, package)

    def kill_appp(self, package):
        self.ldconsole.kill_app(self.index, package)

    def pull(self, remote, local):
        self.ldconsole.pull(self.index, remote, local)

    def push(self, local, remote):
        self.ldconsole.push(self.index, local, remote)

    def dumpsys_window_windows(self):
        cmd = ["dumpsys", "window", "windows"]
        output, _ = self.ldconsole.adb_shell(cmd)
        return output

    def tap(self, x, y):
        cmd = ["input", "tap", str(x), str(y)]
        self.ldconsole.adb_shell(cmd)
        sleep(0.5)

    def long_press(self, x, y):
        self.swipe(x, y, x, y, 750)

    def swipe(self, x1, y1, x2, y2, time=None):
        cmd = ["input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        if time:
            cmd.append(str(time))
        self.ldconsole.adb_shell(cmd)
        sleep(0.5)

    def input_keyevent(self, keyevent: KeyEvent):
        cmd = ["input", "keyevent", str(keyevent.value)]
        self.ldconsole.adb_shell(cmd)
        sleep(0.5)

    def home(self):
        self.input_keyevent(KeyEvent.KEYCODE_HOME)

    def back(self):
        self.input_keyevent(KeyEvent.KEYCODE_BACK)

    def delete(self):
        self.input_keyevent(KeyEvent.KEYCODE_DEL)


class LDPlayerManger:
    """设备如何管理
    管理哪些设备？
    1. 指定设备。
    2. 如果设备没有启动，则启动设备。
    """

    def __init__(self, infos: list[LDPlayerInfo]):
        self._infos = infos
        self._devices = {}
        self._init()

    def _init(self):
        self._devices.clear()
        for info in self._infos:
            self._devices.add(LDPlayer(info))
            # TODO 如果没有启动，则启动；如果卡死，则重启。

        logger.debug("初始完毕")

    def get_total(self) -> int:
        return len(self._devices)

    def get_free_device(self) -> LDPlayer | None:
        for device in self._devices:
            if self._devices[device] == DeviceState.Free:
                self._devices[device] = DeviceState.Busy
                logger.debug(f"free device: {device}")
                return device

    def free_busy_device(self, device: LDPlayer):
        if device not in self._devices:
            return
        self._devices[device] = DeviceState.Free

    def update(self):
        # TODO 对所有的设备进行检查
        # NOTE 仅仅处理指定的设备。
        devices = self._adb.get_devices()
        if devices is None:
            return

        for item in devices:
            name = item[0]
            if item[1] != "device":
                logger.error(f"设备 {name} offline.")
                continue

            try:
                logger.debug(f"开始初始化设备 {name}")
                device = LDPlayer(name)
            except Exception:
                logger.error(f"设备 {name} 找不到", stack_info=True)
                continue

            if device in self._devices:
                logger.error(f"设备 {name} 已经存在")
                continue
            self._devices[device] = DeviceState.Free
