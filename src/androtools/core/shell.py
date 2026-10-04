"""Android shell 命令字典。

多数方法确实只是把参数拼成 adb 命令执行一行 —— 那部分不藏复杂度，省的是
调用方的打字。但有四个地方有真正的 implementation：sdk 的懒解析与缓存、
install_app 按 SDK 版本决定是否申请运行时权限、run_app 从 dumpsys 里解析主界面
组件、pidof 在没有 pidof 命令时回退解析 ps 输出。

Serial 在每次调用时从 DeviceInfo 现取，不是构造时固定的 —— 夜神模拟器在启动
过程中会改写 DeviceInfo.serial。
"""

from typing import Literal

from androtools import logger
from androtools.android_sdk.platform_tools import AdbRunner, AmbiguousDeviceError
from androtools.cmd.result import CmdResult
from androtools.core.constants import KeyEvent
from androtools.core.device import DeviceInfo


class AndroidShell:
    """对一个 Device 执行 adb / adb shell 命令。"""

    def __init__(self, adb: AdbRunner, info: DeviceInfo) -> None:
        self._adb = adb
        self._info = info
        self._sdk: int | None = None
        self._verified_single = False

    @property
    def serial(self) -> str | None:
        return self._info.serial

    def _checked_serial(self) -> str | None:
        """返回 serial；为空时先确认只有一台设备在线。

        serial 为空等于「让 adb 自己挑」，而 adb 只有在一台设备时才挑得对。
        多台时它会回 more than one device/emulator —— 那条命令直接失败还算好，
        真正危险的是上层把「adb 拒绝了」当成「设备没启动」或者干脆忽略掉，
        于是一个查不到任何东西的 Device 报出了 BOOT_COMPLETED。

        「只有一台」这个结论缓存下来，免得每条 adb 命令都多跑一次 devices -l；
        但歧义**不缓存** —— 缓存错误等于第一次抛完异常之后就静默放行，那正是
        上面要防的事。设备插拔之后要重新判断，换一个 shell 即可。
        """
        if self._info.serial is not None:
            return self._info.serial

        if not self._verified_single:
            attached = self._adb.get_devices()
            if len(attached) > 1:
                raise AmbiguousDeviceError(
                    f"没有指定 serial，而 adb 上有 {len(attached)} 台设备在线："
                    f"{[d[0] for d in attached]}。"
                    "请在 DeviceInfo.serial 里指定其中一台。"
                )
            self._verified_single = True

        return None

    @property
    def sdk(self) -> int:
        """设备运行时的 Android API level，首次访问时才查询；查不到为 -1。"""
        if self._sdk is None:
            self._sdk = self._read_sdk()
        return self._sdk

    def _read_sdk(self) -> int:
        output = self.getprop("ro.build.version.sdk")
        try:
            return int(output)
        except ValueError:
            return -1

    # ------------------------------------------------------------------------ #
    #                              adb 命令                                       #
    # ------------------------------------------------------------------------ #

    def adb(self, cmd: list, timeout: int = 30) -> CmdResult:
        """执行 adb 命令"""
        return self._adb.run_cmd(cmd, self._checked_serial(), timeout)

    def adb_shell(self, cmd: list[str], timeout: int = 30) -> CmdResult:
        """执行 adb shell 命令"""
        if isinstance(cmd, str):
            raise TypeError(f"命令必须是列表：{cmd}")
        assert isinstance(cmd, list)
        return self._adb.run_shell_cmd(cmd, self._checked_serial(), timeout)

    def adb_shell_daemon(self, cmd: list[str]) -> None:
        """在设备上启动一个后台命令，不等待结果"""
        if isinstance(cmd, str):
            raise TypeError(f"命令必须是列表：{cmd}")
        assert isinstance(cmd, list)
        self._adb.run_shell_cmd_daemon(cmd, self._checked_serial())

    def getprop(self, prop: str | None = None) -> str:
        """获取设备属性；prop 为 None 时返回全部属性"""
        if prop:
            result = self.adb_shell(["getprop", prop])
        else:
            result = self.adb(["getprop"])

        return result.output

    # ------------------------------------------------------------------------ #
    #                                 应用                                        #
    # ------------------------------------------------------------------------ #

    def install_app(self, apk_path: str) -> tuple[bool, CmdResult]:
        """安装 apk

        Args:
            apk_path (str): apk 路径

        Returns:
            tuple: (是否成功, 命令结果)
        """
        cmd = ["install", "-r", "-g", "-t", apk_path]
        if self.sdk < 25:
            # NOTE: Android 7 以下不支持 -g（运行时权限）
            cmd = ["install", "-r", "-t", apk_path]
        # NOTE: 安装应用最多1分钟算超时
        result = self.adb(cmd, 60)

        return ("Success" in result.output), result

    def uninstall_app(self, package_name: str) -> bool:
        cmd = ["uninstall", package_name]
        return self.adb(cmd).contain("Success")

    def grant_permission(self, package_name: str, permission: str) -> None:
        self.adb_shell(["pm", "grant", package_name, permission])

    def grant_all_permissions(self, package_name: str) -> None:
        r = self.adb_shell(["pm", "dump", package_name, "|", "grep", "granted=false"])
        for line in r.output.split("\n"):
            line = line.strip()
            if line == "":
                continue
            if "granted=false" not in line:
                continue
            self.grant_permission(package_name, line.split(":")[0])

    def run_app(self, package: str) -> bool:
        """启动一个应用的主界面

        Args:
            package (str): 应用包名

        Returns:
            bool: 找到主界面则启动并返回 True
        """
        result = self.adb_shell(["dumpsys", "package", package])
        out = result.output

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

        self.adb_shell(["am", "start", "-n", activity_name])
        return True

    def kill_app(self, package: str) -> None:
        self.adb_shell(["am", "force-stop", package])

    def list_packages(self, flag: Literal[-1, 0, 1] = -1) -> list[str]:
        """列出设备的应用列表

        Args:
            flag (Literal[-1, 0, 1], 可选): `0` 表示第三方应用，`1` 表示系统应用，
                `-1` 表示所有的应用；默认 `-1`。

        Returns:
            list[str]: 包名列表
        """
        cmd = ["pm", "list", "packages"]
        if flag == 0:
            cmd.append("-3")
        elif flag == 1:
            cmd.append("-s")
        return self.adb_shell(cmd).output.replace("package:", "").split()

    # ------------------------------------------------------------------------ #
    #                                 文件                                        #
    # ------------------------------------------------------------------------ #

    def pull(self, remote: str, local: str) -> None:
        """将文件从设备下载到本地"""
        result = self.adb(["pull", remote, local])
        if not result.contain("pulled"):
            logger.error(" ".join(["pull", remote, local]))
            logger.error(result)

    def push(self, local: str, remote: str) -> None:
        """将文件从本地上传到设备"""
        self.adb(["push", local, remote])

    def rm(self, path: str, isDir: bool = False, force: bool = False) -> None:
        """删除文件

        Args:
            path (str): 文件路径
            isDir (bool, optional): 是否强制删除，默认否. Defaults to False.
        """
        cmd = ["rm"]
        if isDir:
            cmd.append("-r")
        if force:
            cmd.append("-f")
        cmd.append(path)
        self.adb_shell(cmd)

    def ls(self, path: str) -> CmdResult | str:
        result = self.adb_shell(["ls", path])
        if result.has_error():
            return result
        return result.output

    def mkdir(self, path: str) -> None:
        self.adb_shell(["mkdir", path])

    def screencap(self, save_dir: str, filename: str) -> None:
        """截图，并保存到指定目录

        Args:
            save_dir (str): 图片存放目录
            filename (str): 图片名
        """
        self.adb_shell(["mkdir", "-p", save_dir])
        self.adb_shell(["screencap", save_dir + "/" + filename])

    # ------------------------------------------------------------------------ #
    #                                 进程                                        #
    # ------------------------------------------------------------------------ #

    def ps(self) -> str:
        return self.adb_shell(["ps", "-A"]).output

    def pidof(self, process_name: str) -> int:
        result = self.adb_shell(["pidof", process_name])
        if result.contain("not found"):
            lines = self.adb_shell(["ps"]).output.splitlines()
            for line in lines:
                parts = line.split()
                if parts[-1] == process_name:
                    return int(parts[1])
            return -1

        if result.output == "":
            return -1
        return int(result.output)

    def kill(self, pid: int) -> None:
        self.adb_shell(["kill", str(pid)])

    def killall(self, process_name: str) -> str:
        return self.adb_shell(["killall", process_name]).output

    def dumpsys_window_windows(self) -> str:
        return self.adb_shell(["dumpsys", "window", "windows"]).output

    # ------------------------------------------------------------------------ #
    #                                 输入                                        #
    # ------------------------------------------------------------------------ #

    def tap(self, x: int, y: int) -> None:
        self.adb_shell(["input", "tap", str(x), str(y)])

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, time_ms: int | None = None
    ) -> None:
        cmd = ["input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        if time_ms:
            cmd.append(str(time_ms))
        self.adb_shell(cmd)

    def long_press(self, x: int, y: int) -> None:
        self.swipe(x, y, x, y, 750)

    def input_keyevent(self, keyevent: KeyEvent) -> None:
        # 输入事件5s必定超时
        self.adb_shell(["input", "keyevent", str(keyevent.value)], 5)

    def input_text(self, txt: str) -> None:
        self.adb_shell(["input", "text", txt])

    def home(self) -> None:
        self.input_keyevent(KeyEvent.KEYCODE_HOME)

    def back(self) -> None:
        self.input_keyevent(KeyEvent.KEYCODE_BACK)

    def delete(self) -> None:
        self.input_keyevent(KeyEvent.KEYCODE_DEL)
