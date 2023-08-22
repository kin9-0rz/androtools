import os

from androtools.android_sdk.platform_tools import ADB

adb = ADB()
devs = adb.get_devices()
if len(devs) == 0:
    exit()

result, _ = adb.run_shell_cmd(["pm", "list", "packages", "-3"])
lines = result.splitlines()

pbrand, _ = adb.run_shell_cmd(["getprop", "ro.product.brand"])
pname, _ = adb.run_shell_cmd(["getprop", "ro.product.name"])

bk_path = pbrand.strip() + "_" + pname.strip()
if not os.path.exists(bk_path):
    os.mkdir(bk_path)

print("Backup Apps...")
APPS_PATH = os.path.join(bk_path, "Apps")
if not os.path.exists(APPS_PATH):
    os.mkdir(APPS_PATH)

size = len(lines)
counter = 0
for line in lines:
    counter += 1
    print(f"{counter}/{size}", end="\r")
    pkg = line[8:]
    cmd = ["pm", "path", pkg]
    output, _ = adb.run_shell_cmd(cmd)
    app_path = output[8:].strip()
    _cmd = ["pull", app_path, os.path.join(APPS_PATH, pkg + ".apk")]
    output, _ = adb.run_cmd(_cmd)

print()
print("Backup Sdcard:")
bk_sdcard_path = os.path.join(bk_path, "sdcard")
if not os.path.exists(bk_sdcard_path):
    os.mkdir(bk_sdcard_path)

dirs = [
    "Fonts",
    "DCIM",
    "Pictures",
]

# TODO show logs?
for item in dirs:
    print(f" - backup {item}")
    apath = os.path.join("sdcard", item)
    adb.run_cmd(["pull", apath, bk_sdcard_path])
