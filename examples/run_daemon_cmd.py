from androtools.android_sdk.platform_tools import ADB
from androtools import turn_on_logger

turn_on_logger()


adb = ADB()
output, _ = adb.run_cmd(["devices"])
print(output)

adb.run_shell_cmd_daemon(["/data/ocal/tmp/frida-server", "&"])
adb.run_shell_cmd_daemon(["su", "-c", "killall", "frida-server"])
adb.run_shell_cmd_daemon(["su", "-c", "/data/local/tmp/frida-server", "&"])
output, _ = adb.run_shell_cmd(["ps"])
for line in output.splitlines():
    if "frida" in line:
        print(line)

