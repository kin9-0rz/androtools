# Android模拟器、雷电模拟器的基类
from abc import ABC, abstractmethod
from enum import Enum
from time import sleep

from func_timeout import FunctionTimedOut, func_timeout

from androtools.core.constants import KeyEvent


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


class Emu(ABC):
    @abstractmethod
    def __init__(self, info: EmuInfo) -> None:
        pass

    @abstractmethod
    def launch(self):
        """启动模拟器"""

    @abstractmethod
    def close(self):
        """关闭模拟器"""

    @abstractmethod
    def reboot(self):
        """重启模拟器"""

    @abstractmethod
    def get_status(self) -> EmuStatus:
        """获取模拟器状态"""

    def is_crashed(self):
        """判断模拟器是否没响应，如果没响应，则定义为模拟器崩溃"""
        try:
            # 点击HOME键，超过5秒没反应
            func_timeout(5, self.home)
        except FunctionTimedOut:
            return True
        return False

    @abstractmethod
    def install_app(self, path: str):
        """安装应用"""

    @abstractmethod
    def uninstall_app(self, package: str):
        """卸载应用"""

    @abstractmethod
    def run_app(self, package: str):
        """运行应用"""

    @abstractmethod
    def kill_app(self, package: str):
        """杀死应用"""

    @abstractmethod
    def pull(self, remote: str, local: str):
        """将文件从模拟器下载到本地"""

    @abstractmethod
    def push(self, local: str, remote: str):
        """将文件从本地上传到模拟器"""

    @abstractmethod
    def adb(self, cmd: str | list) -> tuple[str, str]:
        """执行 adb 命令"""

    @abstractmethod
    def adb_shell(self, cmd: str | list) -> tuple[str, str]:
        """执行 adb shell 命令"""

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
