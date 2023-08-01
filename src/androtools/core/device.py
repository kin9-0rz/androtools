import logging
from enum import Enum
from time import sleep

from func_timeout import FunctionTimedOut, func_timeout

from androtools.android_sdk.platform_tools import ADB, DeviceType

# API等级，SDK，CodeName
# https://apilevels.com/
Android_API_MAP = {
    14: ("Android 4.0.1", "Ice Cream Sandwich"),
    15: ("Android 4.0.3", "Ice Cream Sandwich"),
    16: ("Android 4.1", "Jelly Bean"),
    17: ("Android 4.2", "Jelly Bean"),
    18: ("Android 4.3", "Jelly Bean"),
    19: ("Android 4.4", "KitKat"),
    20: ("Android 4.4w", "KitKat"),
    21: ("Android 5.0", "Lollipop"),
    22: ("Android 5.1", "Lollipop"),
    23: ("Android 6", "Marshmallow"),
    24: ("Android 7.0", "Nougat"),
    25: ("Android 7.1", "Nougat"),
    26: ("Android 8.0", "Oreo"),
    27: ("Android 8.1", "Oreo"),
    28: ("Android 9", "Pie"),
    29: ("Android 10", "Quince Tart"),
    30: ("Android 11", "Red Velvet Cake"),
    31: ("Android 12", "Snow Cone"),
    32: ("Android 12L", "Snow Cone"),
    33: ("Android 13", "TIRAMISU"),
    34: ("Android 14", "Upside Down Cake"),
}


class STATE(Enum):
    DEVICE = "device"
    RECOVERY = "recovery"
    RESCUE = "rescue"
    SIDELOADING = "sideload"
    BOOTLOADER = "bootloader"
    DISCONNECT = "disconnect"


class TRANSPORT(Enum):
    USB = "usb"
    LOCAL = "local"
    ANY = "any"


class Device:
    def __init__(
        self,
        device_name,
        device_type: DeviceType = DeviceType.Serial,
        adb_path: str = None,
    ):
        self.name = device_name
        self.adb = ADB(adb_path)
        self.adb.set_target_device(device_name, device_type)

        counter = 0
        while self._is_offline():
            if ":" in self.name:  # 如果是网络设备，不等待
                raise RuntimeError("device offline")

            counter += 1
            if counter > 15:
                raise RuntimeError("device offline")
            sleep(1)

        self.sdk = 0
        self._init_sdk()
        self.android_version = "Unknown"
        if result := Android_API_MAP.get(self.sdk):
            self.android_version = result[0]

    def __str__(self) -> str:
        return f"{self.name}-{self.android_version}({self.sdk})"

    def _is_offline(self):
        output, error = self.adb.run_cmd(["get-state"])
        # output: ['device']
        # error: error: device offline
        # error: device 'emulator-5556' not found
        if f"error: device '{self.name}' not found" in error:
            logging.error(f"device '{self.name}' not found")
            return True
        return "device" not in output

    def _init_sdk(self):
        output, error = self.adb.run_shell_cmd(["getprop", "ro.build.version.sdk"])
        if isinstance(output, str):
            self.sdk = int(output)
        elif isinstance(output, list):
            self.sdk = int(output[0])
        return self.sdk

    def is_ok(self):
        try:
            # 点击HOME键，超过5秒没反应
            func_timeout(5, self.home)
        except FunctionTimedOut:
            return False
        return True

    # ---------------------------------- adb 命令 ---------------------------------- #

    def install_apk(self, apk_path: str):
        """安装apk

        Args:
            apk_path (str): apk 路径

        Returns:
            tuple: (is_success, output)
        """
        cmd = ["install", "-r", "-g", "-t", apk_path]
        if self.sdk < 26:
            cmd = ["install", "-r", "-t", apk_path]
        output, _ = self.adb.run_cmd(cmd)

        return "Success" in output, output

    def uninstall_apk(self, package_name):
        cmd = ["uninstall", package_name]
        output, error = self.adb.run_cmd(cmd)
        if "Success" in output:
            return True
        logging.error("".join(cmd))
        logging.error(output)
        logging.error(error)

    def pull(self, source_path, target_path):
        cmd = ["pull", source_path, target_path]
        output, error = self.adb.run_cmd(cmd)
        output = "".join(output)
        if "pulled" in output:
            return True
        logging.error("".join(cmd))
        logging.error(output)
        logging.error(error)

    def wait_for(self, state: STATE, transport: TRANSPORT = TRANSPORT.ANY):
        cmd = "wait-for"
        if transport != TRANSPORT.ANY:
            cmd += f"-{transport.value}"
        cmd += f"-{state}"
        output, error = self.adb.run_cmd([cmd])
        return output, error

    # ------------------------------- am 命令，控制应用 ------------------------------ #

    def start_activity(self, package_name, activity_name):
        # adb shell am start -n com.example.myapp/com.example.myapp.MainActivity
        cmd = ["am", "start", "-n", f"{package_name}/{activity_name}"]
        self.adb.run_shell_cmd(cmd)

    def force_stop_app(self, package_name):
        # adb shell am force-stop com.example.myapp
        cmd = ["am", "force-stop", package_name]
        self.adb.run_shell_cmd(cmd)

    # --------------------------------- Linux 命令 --------------------------------- #
    def rm(self, path):
        self.adb.run_shell_cmd(["rm", path])

    def rm_rf(self, path):
        self.adb.run_shell_cmd(["rm", "-rf", path])

    def ls(self, path):
        output, _ = self.adb.run_shell_cmd(["ls", path])
        return output

    def mkdir(self, path):
        self.adb.run_shell_cmd(["mkdir", path])

    def ps(self):
        output, _ = self.adb.run_shell_cmd(["ps"])
        return output

    def pidof(self, process_name):
        output, _ = self.adb.run_shell_cmd(["pidof", process_name])
        output = output.strip()
        if "pidof: not found" in output:
            output, _ = self.adb.run_shell_cmd(["ps"])
            lines = output.splitlines()
            for line in lines:
                parts = line.split()
                if parts[-1] == process_name:
                    return int(parts[1])
            return
        return None if output == "" else int(output)

    def killall(self, process_name):
        output, _ = self.adb.run_shell_cmd(["killall", process_name])
        return output

    def kill(self, pid):
        cmd = ["kill", str(pid)]
        self.adb.run_shell_cmd(cmd)

    def reboot(self, seconds: int = 60):
        self.adb.run_shell_cmd(["reboot"])
        self.wait_for(STATE.DEVICE)

        count = 0
        while self._is_offline():
            count += 1
            if count > seconds:
                break

        while True:
            if self.is_boot_completed():
                break
            sleep(1)
        sleep(3)

    def is_boot_completed(self) -> bool:
        """判断设备是否处于开机状态"""
        output, _ = self.adb.run_shell_cmd(["getprop", "sys.boot_completed"])
        return "1" in output

    # ------------------------------ dumpsys command ----------------------------- #

    def dumpsys_window_windows(self):
        # adb shell dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'
        cmd = ["dumpsys", "window", "windows"]
        output, _ = self.adb.run_shell_cmd(cmd)
        return output

    # ---------------------------------  模拟点击 ------------------------------------ #

    def tap(self, x, y):
        cmd = ["input", "tap", str(x), str(y)]
        self.adb.run_shell_cmd(cmd)
        # 点击太快,模拟器可能反应不过来.
        sleep(1)

    def home(self):
        cmd = ["input", "keyevent", "KEYCODE_HOME"]
        self.adb.run_shell_cmd(cmd)
        sleep(1)

    def swipe(self, x1, y1, x2, y2):
        cmd = ["input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        self.adb.run_shell_cmd(cmd)
        sleep(1)

    def back(self):
        cmd = ["input", "keyevent", "KEYCODE_BACK"]
        self.adb.run_shell_cmd(cmd)
        sleep(1)

    # TODO 清理最近的任务，有可能高级版本才支持
    # adb shell input keyevent KEYCODE_APP_SWITCH
    # && sleep 1 &&
    # adb shell input keyevent KEYCODE_DEL 在5.1的雷电模拟器中无效


class DeviceState:
    Free = 0
    Busy = 1


class DeviceManager:
    def __init__(self, adb_path: str = None, force: bool = False):
        self._adb = ADB(adb_path)
        self._adb.restart_server(force)
        self._devices = {}
        self.update()

    def get_total(self) -> int:
        return len(self._devices)

    def get_free_device(self) -> Device | None:
        for device in self._devices:
            if self._devices[device] == DeviceState.Free:
                self._devices[device] = DeviceState.Busy
                logging.debug(f"free device: {device}")
                return device

    def free_busy_device(self, device: Device):
        self._devices[device] = DeviceState.Free

    def update(self):
        devices, _ = self._adb.get_devices()
        if devices is None:
            return

        for name in devices:
            try:
                device = Device(name)
            except Exception:
                logging.error(f"device {name} not found")
                continue
            if device in self._devices:
                logging.error(f"device {name} already exists")
                continue
            self._devices[device] = DeviceState.Free
