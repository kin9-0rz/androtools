# Android模拟器、雷电模拟器的基类
from abc import ABC, abstractmethod
from enum import Enum
from time import sleep

from func_timeout import FunctionTimedOut, func_timeout
from loguru import logger

from androtools.core.constants import Android_API_MAP, KeyEvent


class EmuInfo(ABC):
    """模拟器信息"""

    index: str  # 模拟器序号
    name: str  # 模拟器名称
    path: str  # adb 路径；雷电模拟器则是 ldconsole

    @abstractmethod
    def __init__(self, index: str, name: str, path: str) -> None:
        """初始化模拟器信息"""

    @abstractmethod
    def __eq__(self, __value: object) -> bool:
        """判断是否为同一个模拟器"""


class EmuStatus(Enum):
    """模拟器状态"""

    STOP = "0"  # 停止
    RUN = "1"  # 运行
    HANG_UP = "2"  # 挂起
    ERORR = "3"  # 模拟器执行 adb 命令没响应，则为错误，需要重启模拟器
    UNKNOWN = "4"  # 未知

    @staticmethod
    def get(value: str):
        for item in EmuStatus:
            if item.value == value:
                return item
        return EmuStatus.UNKNOWN


class WorkStatus(Enum):
    Free = 0
    Busy = 1


class Emu(ABC):
    def __init__(self, info: EmuInfo) -> None:
        self.info = info
        self._init_sdk()
        self.android_version = "Unknown"
        if result := Android_API_MAP.get(self.sdk):
            self.android_version = result[0]

    def __str__(self) -> str:
        return f"{self.info.name}-{self.android_version}({self.sdk})"

    def _init_sdk(self):
        output, _ = self.adb_shell(["getprop", "ro.build.version.sdk"])
        if isinstance(output, str):
            self.sdk = int(output)
        elif isinstance(output, list):
            self.sdk = int(output[0])
        return self.sdk

    def launch(self):
        """启动模拟器"""
        pass

    def close(self):
        """关闭模拟器"""
        pass

    def reboot(self):
        """重启模拟器"""
        pass

    def get_status(self) -> EmuStatus:
        """获取模拟器状态"""
        return EmuStatus.UNKNOWN

    def is_crashed(self):
        """判断模拟器是否没响应，如果没响应，则定义为模拟器崩溃"""
        try:
            # 点击HOME键，超过5秒没反应
            func_timeout(5, self.home)
        except FunctionTimedOut:
            return True
        return False

    def install_app(self, apk_path: str):
        """安装apk

        Args:
            apk_path (str): apk 路径

        Returns:
            tuple: (is_success, output)
        """
        cmd = ["install", "-r", "-g", "-t", apk_path]
        if self.sdk < 26:
            cmd = ["install", "-r", "-t", apk_path]
        output, _ = self.adb(cmd)

        return "Success" in output, output

    def uninstall_app(self, package_name: str):
        """卸载应用"""
        cmd = ["uninstall", package_name]
        output, error = self.adb(cmd)
        if "Success" in output:
            return True
        logger.error("".join(cmd))
        logger.error(output)
        logger.error(error, stack_info=True)

    def run_app(self, package: str) -> bool:
        """启动一个应用

        Args:
            package (str): 应用包名

        Returns:
            bool: 如果返回True，表示存在主界面；如果返回False，表示不存在主界面
        """
        out, _ = self.adb_shell("dumpsys package {}".format(package))
        out = out.strip()

        activity_start = out.find("android.intent.action.MAIN:")
        if activity_start == -1:
            return False

        # android.intent.action.MAIN:\n\n，从这个字符串的尾部开始找
        activity_start += 31
        activity_end = out.find("\n", activity_start)

        activity_name = None
        for item in out[activity_start:activity_end].strip().split():
            if "/" in item:
                activity_name = item
                break
        if activity_name is None:
            return False

        cmd = ["am", "start", "-n", f"{activity_name}"]
        self.adb_shell(cmd)
        return True

    def kill_app(self, package: str):
        self.adb_shell(f"am force-stop {package}")

    def pull(self, remote: str, local: str):
        """将文件从模拟器下载到本地"""
        cmd = ["pull", remote, local]
        output, error = self.adb(cmd)
        output = "".join(output)
        if "pulled" in output:
            return True
        logger.error("".join(cmd))
        logger.error(output)
        logger.error(error)

    def push(self, local: str, remote: str):
        """将文件从本地上传到模拟器"""
        self.adb(["push", local, remote])

    def adb(self, cmd: str | list) -> tuple[str, str]:
        """执行 adb 命令"""
        return "", ""

    def adb_shell(
        self, cmd: str | list, encoding: str | None = None
    ) -> tuple[str, str]:
        """执行 adb shell 命令"""
        return "", ""

    def rm(self, path: str, isDir: bool = False, force: bool = False):
        """删除文件

        Args:
            path (str): 文件路径
            force (bool, optional): 是否强制删除，默认否. Defaults to False.
        """
        cmd = ["rm"]
        if isDir:
            cmd.append("-r")
        if force:
            cmd.append("-f")
        cmd.append(path)
        self.adb_shell(cmd)

    def ls(self, path: str):
        cmd = ["ls", path]
        output, _ = self.adb_shell(cmd)
        return output

    def mkdir(self, path):
        self.adb_shell(["mkdir", path])

    def ps(self):
        output, _ = self.adb_shell(["ps"])
        return output

    def pidof(self, process_name):
        output, _ = self.adb_shell(["pidof", process_name])
        output = output.strip()
        if "pidof: not found" in output:
            output, _ = self.adb_shell(["ps"])
            lines = output.splitlines()
            for line in lines:
                parts = line.split()
                if parts[-1] == process_name:
                    return int(parts[1])
            return
        return None if output == "" else int(output)

    def killall(self, process_name):
        output, _ = self.adb_shell(["killall", process_name])
        return output

    def kill(self, pid):
        cmd = ["kill", str(pid)]
        self.adb_shell(cmd)

    def dumpsys_window_windows(self):
        cmd = ["dumpsys", "window", "windows"]
        output, _ = self.adb_shell(cmd)
        return output

    def tap(self, x: int, y: int):
        cmd = ["input", "tap", str(x), str(y)]
        self.adb_shell(cmd)
        sleep(0.5)

    def long_press(self, x: int, y: int):
        self.swipe(x, y, x, y, 750)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, time: int | None = None):
        cmd = ["input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        if time:
            cmd.append(str(time))
        self.adb_shell(cmd)
        sleep(0.5)

    def input_keyevent(self, keyevent: KeyEvent):
        cmd = ["input", "keyevent", str(keyevent.value)]
        self.adb_shell(cmd)
        sleep(0.5)

    def input_text(self, txt: str):
        cmd = ["input", "text", txt]
        self.adb_shell(cmd)
        sleep(0.5)

    def home(self):
        self.input_keyevent(KeyEvent.KEYCODE_HOME)

    def back(self):
        self.input_keyevent(KeyEvent.KEYCODE_BACK)

    def delete(self):
        self.input_keyevent(KeyEvent.KEYCODE_DEL)
