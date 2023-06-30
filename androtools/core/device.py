import logging
from time import sleep

from androtools.android_sdk import ADB, DeviceType


class Device:
    def __init__(self, name, device_type: DeviceType = DeviceType.Serial):
        self.name = name
        self.prefix = ""
        self.adb = ADB()
        self.adb.set_target_device(name, device_type)

        while self._is_offline():
            sleep(1)

        self._init_sdk()

    def __str__(self) -> str:
        return f"Device[{self.name}]"

    def _is_offline(self):
        output, error = self.adb.run_cmd(["get-state"])
        # output: ['device']
        # error: error: device offline
        # error: device 'emulator-5556' not found
        if f"error: device '{self.name}' not found" in error:
            logging.error(f"device '{self.name}' not found")
            raise RuntimeError(f"device '{self.name}' not found")
        return "device" not in output

    def _init_sdk(self):
        output, _ = self.adb.run_shell_cmd(["getprop", "ro.build.version.sdk"])
        if isinstance(output, str):
            self.sdk = int(output)
        elif isinstance(output, list):
            self.sdk = int(output[0])
        return self.sdk

    def install_apk(self, apk_path):
        cmd = ["install", "-r", "-g", "-t", apk_path]
        if self.sdk < 26:
            cmd = ["install", "-r", "-t", apk_path]
        output, error = self.adb.run_cmd(cmd)
        if "Success" in output:
            return True
        logging.error("".join(cmd))
        logging.error(output)
        logging.error(error)
        return False

    def start_activity(self, package_name, activity_name):
        # adb shell am start -n com.example.myapp/com.example.myapp.MainActivity
        cmd = ["am", "start", "-n", f"{package_name}/{activity_name}"]
        self.adb.run_shell_cmd(cmd)

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

    def rm(self, path):
        cmd = ["rm", path]
        self.adb.run_shell_cmd(cmd)

    def tap(self, x, y):
        cmd = ["input", "tap", str(x), str(y)]
        self.adb.run_shell_cmd(cmd)
        # 点击太快,模拟器可能反应不过来.
        sleep(1)

    def home(self):
        # adb shell input keyevent KEYCODE_HOME
        cmd = ["input", "keyevent", "KEYCODE_HOME"]
        self.adb.run_shell_cmd(cmd)
        sleep(1)

    def swipe(self, x1, y1, x2, y2):
        cmd = ["input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        self.adb.run_shell_cmd(cmd)
        sleep(1)

    def back(self):
        # adb shell input keyevent KEYCODE_BACK
        cmd = ["input", "keyevent", "KEYCODE_BACK"]
        self.adb.run_shell_cmd(cmd)
        sleep(1)

    # TODO 清理最近的任务?
    # adb shell input keyevent KEYCODE_APP_SWITCH
    # && sleep 1 &&
    # adb shell input keyevent KEYCODE_DEL 在5.1的雷电模拟器中无效

    def dumpsys_window_windows(self):
        # adb shell dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'
        cmd = ["dumpsys", "window", "windows"]
        output, _ = self.adb.run_shell_cmd(cmd)
        return output

    def force_stop_app(self, package_name):
        # adb shell am force-stop com.example.myapp
        cmd = ["am", "force-stop", package_name]
        self.adb.run_shell_cmd(cmd)


class DeviceState:
    Free = 0
    Busy = 1


class DeviceManager:
    def __init__(self):
        self._adb = ADB()
        self._adb.restart_server()
        self._devices = {}
        self.update()

    def get_all_devices(self):
        return self._all_devices

    def get_total(self):
        return len(self._devices)

    def get_busy_devices(self):
        return self._busy_devices

    def get_free_device(self):
        for device in self._devices:
            if self._devices[device] == DeviceState.Free:
                self._devices[device] = DeviceState.Busy
                logging.debug(f"free device: {device}")
                return device

    def free_busy_device(self, device: Device):
        self._devices[device] = DeviceState.Free

    def update(self):
        devices, _ = self._adb.list_devices()

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
