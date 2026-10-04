"""把设备上的第三方应用备份成 apk。

用法：
    python examples/backup.py <serial> [输出目录] [只备份前 N 个应用]

不带 serial 时，如果只有一台设备在线就用它；多台在线会要求你显式指定。
输出目录默认是系统临时目录下的 backup-<serial>，不会往仓库里倒东西 ——
一份完整备份轻易就是几个 GB。
"""

import os
import sys
import tempfile

from androtools import enable_console_logging
from androtools.android_sdk.platform_tools import ADB
from androtools.core import Device, DeviceInfo


def pick_serial(adb: ADB) -> str | None:
    devices = adb.get_devices()
    usable = [d for d in devices if d[1] == "device"]
    if not usable:
        print("没有可用的设备")
        return None

    if len(sys.argv) > 1:
        wanted = sys.argv[1]
        if not any(d[0] == wanted for d in usable):
            print(f"设备 {wanted} 不在线。当前在线：{[d[0] for d in usable]}")
            return None
        return wanted

    if len(usable) > 1:
        print(f"有多台设备在线：{[d[0] for d in usable]}")
        print("请用 serial 参数指定其中一台，例如：python examples/backup.py <serial>")
        return None

    return usable[0][0]


def main() -> None:
    enable_console_logging()

    adb = ADB()
    serial = pick_serial(adb)
    if serial is None:
        return

    assert adb.bin_path is not None
    info = DeviceInfo(name=serial, serial=serial, adb_path=adb.bin_path)
    device = Device(info)
    print(f"设备 {device} 状态 {device.get_status()}")

    out_root = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        tempfile.gettempdir(), f"backup-{serial}"
    )
    apks_dir = os.path.join(out_root, "Apps")
    os.makedirs(apks_dir, exist_ok=True)

    packages = device.shell.list_packages(flag=0)
    if len(sys.argv) > 3:
        packages = packages[: int(sys.argv[3])]
    print(f"备份 {len(packages)} 个第三方应用到 {apks_dir}")

    total = len(packages)
    for index, package in enumerate(packages, start=1):
        result = device.shell.adb_shell(["pm", "path", package])
        apk_path = result.output.strip()
        if not apk_path.startswith("package:"):
            print(f"  [{index}/{total}] {package} 拿不到 apk 路径，跳过")
            continue

        device.shell.pull(apk_path[len("package:") :], os.path.join(apks_dir, f"{package}.apk"))
        print(f"  [{index}/{total}] {package}")

    sdcard_dir = os.path.join(out_root, "sdcard")
    for item in ("DCIM", "Pictures", "Documents"):
        device.shell.pull(f"/sdcard/{item}", sdcard_dir)

    print(f"完成 -> {out_root}")


if __name__ == "__main__":
    main()