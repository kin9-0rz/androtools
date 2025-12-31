import time
from typing import Literal

from androtools.core.delegate import ABCDelegate


class AppDelegate(ABCDelegate):
    def list_packages(self, flag: Literal[-1, 0, 1] = -1) -> list[str]:
        """列出设备的应用列表

        Args:
            flag (Literal[-1, 0, 1], 可选): `0` 表示第三方应用，`1` 表示系统应用，`-1` 表示所有的应用；默认 `-1`。

        Returns:
            list[str]: 包名列表
        """
        cmd = ["pm", "list", "packages"]
        if flag == 0:
            cmd.append("-3")
        elif flag == 1:
            cmd.append("-s")
        output = self.adb.adb_shell(cmd).output

        return output.replace("package:", "").split()

    def get_system_apps(self):
        """获取系统应用包名列表"""
        return self.list_packages(-1)

    def get_3rd_party_apps(self):
        """获取第三方应用包名列表"""
        return self.list_packages(0)

    def install_app(self, apk_path):
        """安装apk

        Args:
            apk_path (str): apk 路径

        Returns:
            tuple: (is_success, output)
        """
        if self.adb.device.sdk is None:
            self.sdk = self.adb.device.get_sdk()

        cmd = ["install", "-r", "-g", "-t", apk_path]
        if self.sdk < 25:
            cmd = ["install", "-r", "-t", apk_path]
        # NOTE: 安装应用最多1分钟算超时
        result = self.adb.adb_shell(cmd, timeout=60)

        if "Success" in result.output:
            return True, result

        return False, result

    def uninstall_app(self, package_name: str):
        """卸载应用"""
        cmd = ["uninstall", package_name]
        result = self.adb.adb(cmd)
        return result.contain("Success")

    def launch_app(self, package_name: str):
        """使用 monkey 启动应用"""
        cmd = [
            "monkey",
            "-p",
            package_name,
            "-c",
            "android.intent.category.LAUNCHER",
            1,
        ]
        self.adb.adb_shell(cmd)
        time.sleep(1)

    def run_app(self, package: str) -> bool:
        """启动一个应用

        Args:
            package (str): 应用包名

        Returns:
            bool: 如果返回True，表示存在主界面；如果返回False，表示不存在主界面
        """
        result = self.adb.adb_shell(["dumpsys", "package", package])
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

        cmd = ["am", "start", "-n", f"{activity_name}"]
        self.adb.adb_shell(cmd)
        return True

    def stop_app(self, package: str):
        self.adb.adb_shell(["am", "force-stop", package])

    def get_current_app(self):
        cmd = ["dumpsys", "window"]
        result = self.adb.adb_shell(cmd)
        for line in result.output.split("\n"):
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                # TODO: 获取包名
                pass
        return "Home"

    def grant_permission(self, package_name: str, permission: str):
        self.adb.adb_shell(["pm", "grant", package_name, permission])

    def grant_all_permissions(self, package_name: str):
        r = self.adb.adb_shell(
            ["pm", "dump", package_name, "|", "grep", "granted=false"]
        )
        for line in r.output.split("\n"):
            line = line.strip()
            if line == "":
                continue
            if "granted=false" not in line:
                continue
            perm = line.split(":")[0]
            self.grant_permission(package_name, perm)
