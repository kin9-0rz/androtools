"""在设备上启动 frida-server，并在进程列表里找到它。

真机和已 root 的模拟器都需要。frida-server 的二进制要先自己 push 上去。

用法：
    python examples/run_daemon_cmd.py <serial>
"""

import sys
import time

from androtools import enable_console_logging
from androtools.android_sdk.platform_tools import ADB
from androtools.core import Device, DeviceInfo

SERVER_PATH = "/data/local/tmp/frida-server"


def main() -> None:
    enable_console_logging()

    if len(sys.argv) < 2:
        print(__doc__)
        return

    serial = sys.argv[1]
    adb = ADB()
    assert adb.bin_path is not None
    device = Device(DeviceInfo(name=serial, serial=serial, adb_path=adb.bin_path))

    print(f"设备 {device} 状态 {device.get_status()}")
    if not device.is_boot():
        print("设备不在线，先确认 adb devices 能看到它")
        return

    device.shell.adb_shell(["su", "-c", f"killall frida-server"])
    time.sleep(1)

    # 后台命令：发出去就不等结果了
    device.shell.adb_shell_daemon(["su", "-c", f"{SERVER_PATH} &"])
    time.sleep(2)

    for line in device.shell.ps().splitlines():
        if "frida" in line:
            print(line)


if __name__ == "__main__":
    main()