from enum import Enum
from time import sleep

from func_timeout import FunctionTimedOut, func_timeout
from loguru import logger

from androtools.android_sdk.platform_tools import ADB, DeviceType
from androtools.core.constants import Android_API_MAP, DeviceState, KeyEvent


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


class G_STATE(Enum):
    """使用 get_state 方法获取的状态。"""

    DEVICE = "device"
    OFFLINE = "offline"
    BOOTLOADER = "bootloader"
    NOFOUND = "nofound"
    UNKNOWN = "unknown"


# adb -s emulator-5554 emu avd id
# Pixel_4_XL_API_22
# OK
# ❯ adb -s emulator-5554 emu avd name
# Pixel_4_XL_API_22
# NOTE 设备重新启动之后，端口有可能会发生。
# 启动模拟器，然后，通过 adb devices -l 获取所有的模拟器信息。
# 在通过 adb -s emulator-5554 emu avd id，来重新映射模拟器序列号。
class DeviceInfo:
    """设备信息，通过 adb devices -l 获取以下信息"""

    name: str  # 设备名，用于启动模拟器
    serial: str  # 设备序列号，用于 adb
    transport_id: str  # 设备传输ID，用于 adb
    model: str  # Android_SDK_built_for_x86 2209129SC
    product: str  # sdk_google_phone_x86 ziyi
    device: str  # generic_x86 ziyi


# TODO 杀死模拟器的方式
# @Pixel_XL_API_30，获取进程pid，杀死pid。
# 再通过emulator来启动。
# 雷电模拟器的启动方式不一样。


class Device:
    def __init__(
        self,
        device_name: str,
        device_type: DeviceType = DeviceType.Serial,
        adb_path: str | None = None,
    ):
        assert isinstance(device_name, str)
        self.name = device_name
        self.adb = ADB(adb_path)
        self.adb.set_target_device(device_name, device_type)

        # 设备初始化，则表示设备一定存在
        state = self._get_state()
        logger.debug(f"设备 {self.name} 状态: {state.value}")
        if state != G_STATE.DEVICE:
            raise RuntimeError(f"Device is {state.value}")

        self.sdk = 0
        self._init_sdk()
        self.android_version = "Unknown"
        if result := Android_API_MAP.get(self.sdk):
            self.android_version = result[0]

    def __str__(self) -> str:
        return f"{self.name}-{self.android_version}({self.sdk})"

    def check_device_status(self, num=5):
        """执行命令之前，先确认一下设备的状态。

        Args:
            num (int, optional): _description_. Defaults to 5.

        Returns:
            _type_: _description_
        """
        state = self._get_state()
        logger.debug(f"check_device_status - {self.name} 状态 : {state.value}")
        if state == G_STATE.DEVICE:
            return True

        counter = 0
        is_ok = False
        while counter < num:
            counter += 1
            devices = self.adb.get_devices()
            for item in devices:
                if item[0] == self.name and item[1] == "device":
                    is_ok = True
                    break

            if is_ok:
                break

        return is_ok

    def _get_state(self):
        output, error = self.adb.run_cmd(["get-state"])
        # output: ['device']
        # error: error: device offline
        # error: device 'emulator-5556' not found

        output = "".join(output) + error

        # 设备丢失，需要等待，或者重启 adb
        if "not found" in error:
            return G_STATE.NOFOUND

        if "device" in output:
            return G_STATE.DEVICE

        # 设备无法控制
        if "offline" in output:
            return G_STATE.DEVICE

        # 设备可以重启
        if "bootloader" in output:
            return G_STATE.BOOTLOADER

        return G_STATE.UNKNOWN

    def _run_shell_cmd(self, cmd: list):
        logger.debug(f"run shell cmd : {cmd}")
        if not self.check_device_status():
            raise RuntimeError(f"{self.name} 设备丢失。")
        return self.adb.run_shell_cmd(cmd)

    def _run_cmd(self, cmd: list):
        logger.debug(f"run cmd : {str(cmd)}")
        if not self.check_device_status():
            raise RuntimeError(f"{self.name} 设备丢失。")
        return self.adb.run_cmd(cmd)

    def _init_sdk(self):
        output, _ = self._run_shell_cmd(["getprop", "ro.build.version.sdk"])
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
        output, _ = self._run_cmd(cmd)

        return "Success" in output, output

    def uninstall_apk(self, package_name):
        cmd = ["uninstall", package_name]
        output, error = self._run_cmd(cmd)
        if "Success" in output:
            return True

        logger.error("".join(cmd))
        logger.error(output)
        logger.error(error, stack_info=True)

    def pull(self, source_path, target_path):
        cmd = ["pull", source_path, target_path]
        output, error = self._run_cmd(cmd)
        output = "".join(output)
        if "pulled" in output:
            return True
        logger.error("".join(cmd))
        logger.error(output)
        logger.error(error)

    def wait_for(self, state: STATE, transport: TRANSPORT = TRANSPORT.ANY):
        cmd = "wait-for"
        if transport != TRANSPORT.ANY:
            cmd += f"-{transport.value}"
        cmd += f"-{state}"
        output, error = self._run_cmd([cmd])
        return output, error

    # ------------------------------- am 命令，控制应用 ------------------------------ #

    def start_activity(self, package_name, activity_name):
        # adb shell am start -n com.example.myapp/com.example.myapp.MainActivity
        cmd = ["am", "start", "-n", f"{package_name}/{activity_name}"]
        self._run_shell_cmd(cmd)

    def force_stop_app(self, package_name):
        # adb shell am force-stop com.example.myapp
        cmd = ["am", "force-stop", package_name]
        self._run_shell_cmd(cmd)

    # --------------------------------- Linux 命令 --------------------------------- #
    def rm(self, path):
        self._run_shell_cmd(["rm", path])

    def rm_rf(self, path):
        self._run_shell_cmd(["rm", "-rf", path])

    def ls(self, path):
        output, _ = self._run_shell_cmd(["ls", path])
        return output

    def mkdir(self, path):
        self._run_shell_cmd(["mkdir", path])

    def ps(self):
        output, _ = self._run_shell_cmd(["ps"])
        return output

    def pidof(self, process_name):
        output, _ = self._run_shell_cmd(["pidof", process_name])
        output = output.strip()
        if "pidof: not found" in output:
            output, _ = self._run_shell_cmd(["ps"])
            lines = output.splitlines()
            for line in lines:
                parts = line.split()
                if parts[-1] == process_name:
                    return int(parts[1])
            return
        return None if output == "" else int(output)

    def killall(self, process_name):
        output, _ = self._run_shell_cmd(["killall", process_name])
        return output

    def kill(self, pid):
        cmd = ["kill", str(pid)]
        self._run_shell_cmd(cmd)

    def reboot(self):
        self._run_shell_cmd(["reboot"])
        sleep(5)

    def is_boot_completed(self) -> bool:
        """判断设备是否处于开机状态"""
        output, _ = self._run_shell_cmd(["getprop", "sys.boot_completed"])
        return "1" in output

    # ------------------------------ dumpsys command ----------------------------- #

    def dumpsys_window_windows(self):
        # adb shell dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'
        cmd = ["dumpsys", "window", "windows"]
        output, _ = self._run_shell_cmd(cmd)
        return output

    # ---------------------------------  模拟点击 ------------------------------------ #
    # 点击太快,模拟器可能反应不过来，所以，每次操作都等待0.5秒
    def tap(self, x, y):
        cmd = ["input", "tap", str(x), str(y)]
        self._run_shell_cmd(cmd)
        sleep(0.5)

    def long_press(self, x, y):
        """长按"""
        self.swipe(x, y, x, y, 750)

    def swipe(self, x1, y1, x2, y2, time=None):
        cmd = ["input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        if time:
            cmd.append(str(time))
        self._run_shell_cmd(cmd)
        sleep(0.5)

    def input_keyevent(self, keyevent: KeyEvent):
        cmd = ["input", "keyevent", str(keyevent.value)]
        self._run_shell_cmd(cmd)
        sleep(0.5)

    def input_text(self, txt: str):
        cmd = ["input", "text", txt]
        self._run_cmd(cmd)
        sleep(0.5)

    def home(self):
        self.input_keyevent(KeyEvent.KEYCODE_HOME)

    def back(self):
        self.input_keyevent(KeyEvent.KEYCODE_BACK)

    def delete(self):
        self.input_keyevent(KeyEvent.KEYCODE_DEL)


class DeviceManager:
    def __init__(self, adb_path: str | None = None):
        self._adb = ADB(adb_path)
        self._devices = {}
        self._init()

    def _init(self):
        self._devices.clear()
        count = 0
        logger.debug("正在初始化模拟器...")
        while count < 5:
            count += 1
            if self._check_devices():
                break
        logger.debug("初始完毕")
        self.update()

    def _check_devices(self):
        devices = self._adb.get_devices()
        if devices is None:
            return
        flag = True
        for item in devices:
            if item is None:
                continue

            if item[1] == "offline":
                flag = False
                break
            if ":" in item:
                flag = False
                break

        if flag:
            return flag

        self._adb.restart_server(True)
        return flag

    def get_total(self) -> int:
        return len(self._devices)

    def get_free_device(self) -> Device | None:
        for device in self._devices:
            if self._devices[device] == DeviceState.Free:
                self._devices[device] = DeviceState.Busy
                logger.debug(f"free device: {device}")
                return device

    def free_busy_device(self, device: Device):
        if device not in self._devices:
            return
        self._devices[device] = DeviceState.Free

    def update(self):
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
                device = Device(name)
            except Exception:
                logger.error(f"设备 {name} 找不到", stack_info=True)
                continue

            if device in self._devices:
                logger.error(f"设备 {name} 已经存在")
                continue
            self._devices[device] = DeviceState.Free
