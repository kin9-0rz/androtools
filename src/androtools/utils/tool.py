import os
import time
from androtools.core.device import Device
from androtools.android_sdk.platform_tools import ADB


class Iptables:
    """

    参考：
    https://evilpan.com/2023/01/30/android-iptables/
    https://zhuanlan.zhihu.com/p/419923518
    """

    def __init__(self, device: Device):
        self.device = device

    def reload(self):
        self.device.adb_shell(
            ADB.build_su_cmd(["iptables-save", ">", "/data/local/tmp/iptables.rules"])
        )
        self.device.adb_shell(
            ADB.build_su_cmd(
                ["iptables-restore", "<", "/data/local/tmp/iptables.rules"]
            )
        )

    def clear(self):
        self.device.adb_shell(ADB.build_su_cmd(["iptables", "-F"]))
        self.reload()

    def add(self, package_name: str) -> str:
        # dumpsys package $2 | grep userId | sed "s/[ \t]*userId=//g"
        r = self.device.adb_shell(["dumpsys", "package", package_name]).output
        user_id = r.split("userId=")[1].split("\n")[0]

        # iptables -A OUTPUT -m owner --uid-owner $2 -j CONNMARK --set-mark 1
        self.device.adb_shell(
            ADB.build_su_cmd(
                [
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-m",
                    "owner",
                    "--uid-owner",
                    user_id,
                    "-j",
                    "CONNMARK",
                    "--set-mark",
                    user_id,
                ]
            )
        )
        # iptables -A INPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.device.adb_shell(
            ADB.build_su_cmd(
                [
                    "su",
                    "0",
                    "iptables",
                    "-A",
                    "INPUT",
                    "-m",
                    "connmark",
                    "--mark",
                    user_id,
                    "-j",
                    "NFLOG",
                    "--nflog-group",
                    user_id,
                ]
            )
        )
        # iptables -A OUTPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.device.adb_shell(
            ADB.build_su_cmd(
                [
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-m",
                    "connmark",
                    "--mark",
                    user_id,
                    "-j",
                    "NFLOG",
                    "--nflog-group",
                    user_id,
                ]
            )
        )
        self.reload()

        return user_id


class Tcpdump:
    """对某个应用进行抓包"""

    def __init__(self, device: Device, package_name: str):
        self.device = device
        self.package_name = package_name
        self.pcap_path = "/data/local/tmp/net.pcap"
        self.iptables = Iptables(self.device)

    def start_capture(self):
        user_id = self.iptables.add(self.package_name)
        time.sleep(1)
        self.device.adb_shell_daemon(
            ADB.build_su_cmd(
                [
                    "tcpdump",
                    "-i",
                    f"nflog:{user_id}",
                    "-U",
                    "-w",
                    self.pcap_path,
                    "&",
                ]
            )
        )

    def stop_capture(self):
        self.device.adb_shell(ADB.build_su_cmd(["killall", "tcpdump"]))
        self.iptables.clear()

    def pull_pcap_file(self, output: str, name: str = "net.pcap"):
        self.device.pull(self.pcap_path, os.path.join(output, name))
